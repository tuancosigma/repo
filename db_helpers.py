"""Database helper functions and aggregation pipeline builders."""
from datetime import datetime, timezone
from pymongo import MongoClient
import logging
from config import MONGO_URI, MONGODB_TIMEOUT_MS, MONGODB_SOCKET_TIMEOUT_MS, MAX_POOL_SIZE, MIN_POOL_SIZE, COLLECTIONS

logger = logging.getLogger(__name__)

# Global MongoDB client with connection pooling
_mongo_client = None


def get_mongo_client():
    """Get MongoDB client with connection pooling (singleton pattern)."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS,
            maxPoolSize=MAX_POOL_SIZE,
            minPoolSize=MIN_POOL_SIZE,
            maxIdleTimeMS=45000,
            connectTimeoutMS=MONGODB_TIMEOUT_MS,
            socketTimeoutMS=MONGODB_SOCKET_TIMEOUT_MS  # 10 min default for large credentials queries
        )
    return _mongo_client


def get_collection(collection_name):
    """Get MongoDB collection by name from source collections."""
    client = get_mongo_client()
    coll_config = COLLECTIONS.get(collection_name, {})
    db = client[coll_config.get('db', collection_name)]
    return db[coll_config.get('coll', collection_name)]


def build_count_pipeline(date_field, start, end, additional_filters=None, use_gte_only=False):
    """Build aggregation pipeline for counting documents in date range.
    
    This matches the logic from count_import_daily.py:
    - For ZIP archives: filter by inserted_time >= (now - 24h) - uses only $gte like script
    - Uses datetime objects for inserted_time (archives), created_date (alerts)
    - Uses ISO string for harvest_date (credentials)
    
    IMPORTANT: All datetime objects are normalized to UTC 0 before querying.
    MongoDB will compare datetime values correctly regardless of stored timezone.
    
    Args:
        date_field: Field name for date filtering (e.g., 'inserted_time', 'harvest_date', 'created_date')
        start: Start datetime (UTC 0, e.g., now - timedelta(hours=24) for daily)
        end: End datetime (UTC 0, e.g., now) - only used if use_gte_only=False
        additional_filters: Dict of additional match filters
        use_gte_only: If True, use only $gte (like script count_zip_import_daily.py). If False, use both $gte and $lte.
    """
    # Ensure start is UTC 0 datetime object
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    
    # Ensure end is UTC 0 datetime object (only if using range)
    if not use_gte_only:
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        else:
            end = end.astimezone(timezone.utc)
    
    # Special handling for harvest_date (credentials) - always use ISO string format
    if date_field == 'harvest_date':
        # Convert datetime to ISO string for harvest_date field
        start_value = start.isoformat() if isinstance(start, datetime) else start
        if not use_gte_only:
            end_value = end.isoformat() if isinstance(end, datetime) else end
    else:
        # For other date fields (inserted_time for archives, created_date for alerts), use datetime objects
        # MongoDB will compare datetime values correctly regardless of stored timezone (+08:00 vs UTC 0)
        # This matches count_import_daily.py: query = {"inserted_time": {"$gte": one_day_ago}}
        start_value = start
        if not use_gte_only:
            end_value = end
    
    # Build match filter
    # For ZIP archives (inserted_time): use only $gte like script count_zip_import_daily.py
    # For other queries: use both $gte and $lte for explicit range
    if use_gte_only:
        # Match script logic: only $gte (no upper bound)
        match_filters = {date_field: {"$gte": start_value}}
    else:
        # Dashboard logic: both $gte and $lte for explicit range
        match_filters = {date_field: {"$gte": start_value, "$lte": end_value}}
    
    if additional_filters:
        match_filters.update(additional_filters)
    
    # Log at DEBUG to reduce I/O overhead in production
    logger.debug(f"Query filter for {date_field}: {match_filters}")
    
    return [
        {"$match": match_filters},
        {"$count": "total"}
    ]


def build_hwid_pipeline(start, end):
    """Build aggregation pipeline for counting unique HWIDs from detections array.
    
    IMPORTANT: detections is an array, and we need to:
    1. Unwind the detections array
    2. Extract id from various paths (host.id, source.host.id, etc.)
    3. Count unique IDs (if duplicate, don't count again)
    
    Structure in MongoDB:
    - detections: Array
      - detections[].host.id
      - detections[].source.host.id (possible)
      - Other possible paths for id
    
    Args:
        start: Start datetime (UTC 0)
        end: End datetime (UTC 0)
    
    Returns:
        Aggregation pipeline for unique HWID count
    """
    # Ensure start and end are UTC 0 datetime objects
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)
    
    pipeline = [
        # Step 1: Match date range and ensure detections exists and is array
        {"$match": {
            "created_date": {"$gte": start, "$lte": end},
            "detections": {"$exists": True, "$ne": None, "$type": "array", "$not": {"$size": 0}}
        }},
        # Step 2: Unwind detections array to process each detection element
        {"$unwind": "$detections"},
        # Step 3: Extract id from various possible paths
        # Priority: detections.host.id > detections.source.host.id > other paths
        {"$project": {
            "hwid": {
                "$ifNull": [
                    "$detections.host.id",
                    {"$ifNull": [
                        "$detections.source.host.id",
                        None
                    ]}
                ]
            }
        }},
        # Step 4: Filter out documents without hwid (null, empty string, or missing)
        {"$match": {
            "hwid": {"$exists": True, "$ne": None, "$ne": "", "$type": "string"}
        }},
        # Step 5: Group by hwid to get unique IDs only
        # This ensures if same ID appears multiple times, it's only counted once
        {"$group": {
            "_id": "$hwid"
        }},
        # Step 6: Count unique HWIDs
        {"$count": "total"}
    ]
    
    logger.debug(f"HWID pipeline: Match date range {start.isoformat()} to {end.isoformat()}")
    
    return pipeline


def build_stats_pipelines(start, end):
    """Build all statistics aggregation pipelines.
    
    IMPORTANT: ZIP archives uses only $gte (like script count_zip_import_daily.py)
    to match the exact query logic: {"inserted_time": {"$gte": one_day_ago}}
    
    Returns:
        dict: Dictionary with pipeline functions for each collection
    """
    pipelines = {
        # ZIP archives: use only $gte (no upper bound) - matches script count_zip_import_daily.py
        'zip_import': build_count_pipeline(
            'inserted_time', start, end, None, use_gte_only=True
        ),
        # Decompressed: use range ($gte and $lte) with additional filter
        'decompressed': build_count_pipeline(
            'inserted_time', start, end, 
            {'is_decompressed': True},
            use_gte_only=False
        ),
        # Credentials: use range ($gte and $lte) for performance
        # $gte only causes timeout because it scans hundreds of millions of records
        # Range query is much faster and still accurate for the date period
        'credentials': build_count_pipeline(
            'harvest_date', start, end, None,
            use_gte_only=False
        ),
        # HWID: use custom pipeline to count unique IDs from detections array
        'hwid': build_hwid_pipeline(start, end)
    }
    
    return pipelines


def execute_stats_queries(start, end):
    """Execute all statistics queries and return results.
    
    Args:
        start: Start datetime (UTC 0)
        end: End datetime (UTC 0)
    
    Returns:
        dict: Statistics dictionary with counts
    """
    from datetime import datetime, timezone
    import logging
    logger = logging.getLogger(__name__)
    
    # Ensure start and end are UTC 0 datetime objects
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)
    
    pipelines = build_stats_pipelines(start, end)
    
    archives_col = get_collection('archives')
    credentials_col = get_collection('credentials')
    alerts_col = get_collection('alerts')
    
    logger.debug(f"Executing stats queries - start={start.isoformat()}, end={end.isoformat()}")
    
    # Execute each query with error handling and detailed logging
    try:
        zip_result = list(archives_col.aggregate(pipelines['zip_import'], allowDiskUse=True, maxTimeMS=10000))
        zip_count = zip_result[0]['total'] if zip_result else 0
        logger.debug(f"ZIP import result: {zip_count}")
    except Exception as e:
        logger.error(f"Error executing ZIP import query: {e}", exc_info=True)
        zip_count = 0
    
    try:
        decompressed_result = list(archives_col.aggregate(pipelines['decompressed'], allowDiskUse=True, maxTimeMS=10000))
        decompressed_count = decompressed_result[0]['total'] if decompressed_result else 0
        logger.debug(f"Decompressed result: {decompressed_count}")
    except Exception as e:
        logger.error(f"Error executing decompressed query: {e}", exc_info=True)
        decompressed_count = 0
    
    try:
        # Credentials query - no maxTimeMS, let it run to completion
        # Data volume can be hundreds of millions, query may take several minutes
        credentials_result = list(credentials_col.aggregate(
            pipelines['credentials'], 
            allowDiskUse=True
        ))
        credentials_count = credentials_result[0]['total'] if credentials_result else 0
        logger.debug(f"Credentials result: {credentials_count}")
    except Exception as e:
        # Log timeout errors but don't fail the entire stats request
        error_msg = str(e)
        if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
            logger.warning(f"Credentials query timeout in stats (returning 0) - query may be too slow for date range")
        else:
            logger.error(f"Error executing credentials query: {e}", exc_info=True)
        credentials_count = 0
    
    try:
        hwid_result = list(alerts_col.aggregate(pipelines['hwid'], allowDiskUse=True, maxTimeMS=10000))
        hwid_count = hwid_result[0]['total'] if hwid_result else 0
        logger.debug(f"HWID result: {hwid_count}")
    except Exception as e:
        error_msg = str(e)
        if 'timed out' in error_msg.lower() or 'timeout' in error_msg.lower():
            logger.warning(f"HWID query timeout (returning 0)")
        else:
            logger.error(f"Error executing HWID query: {e}", exc_info=True)
        hwid_count = 0
    
    logger.info(f"Stats query results summary - zip_import={zip_count}, decompressed={decompressed_count}, "
               f"credentials={credentials_count}, hwid={hwid_count}")
    
    return {
        'zip_import': zip_count,
        'decompressed': decompressed_count,
        'credentials': credentials_count,
        'hwid': hwid_count
    }
