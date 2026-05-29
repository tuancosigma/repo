"""Flask web application for MongoDB Statistics Dashboard.

TIMEZONE POLICY:
- All datetime operations use UTC timezone (datetime.now(timezone.utc))
- All MongoDB queries use UTC datetime objects (UTC 0, +00:00)
- All date comparisons and calculations are in UTC 0
- Frontend displays dates in UTC format (with "UTC" suffix)
- France is UTC+1 (UTC+2 in summer), but all internal operations use UTC 0
- When displaying dates to users, they are shown in UTC 0 to maintain consistency
- IMPORTANT: All datetime objects read from MongoDB are normalized to UTC 0 using normalize_to_utc()
  This ensures that even if MongoDB stores dates with +08:00 (or any other timezone),
  all comparisons and operations use UTC 0 (+00:00) for consistency.
"""
from flask import Flask, render_template, jsonify, request, send_file, Response
from datetime import datetime, timedelta, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus.flowables import HRFlowable
from reportlab.pdfgen import canvas
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics import renderPDF
from io import BytesIO
import os
import logging
import json

from config import COLLECTIONS
from db_helpers import get_mongo_client, get_collection, execute_stats_queries, build_count_pipeline
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Persistent File Cache with TTL
_cache = {}
CACHE_TTL_STATS = 86400  # 24 hours for stats (persistent file cache)
CACHE_TTL_CHART = 300  # 5 minutes for chart data (less frequently updated)
PERSISTENT_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'static', 'persistent_cache.json')


def load_persistent_cache():
    """Load cache entries from local JSON file."""
    global _cache
    if os.path.exists(PERSISTENT_CACHE_FILE):
        try:
            with open(PERSISTENT_CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                _cache = {k: (v[0], v[1]) for k, v in loaded.items()}
                logger.info(f"Loaded {len(_cache)} entries from persistent cache file")
        except Exception as e:
            logger.warning(f"Error loading persistent cache file: {e}")


def save_persistent_cache():
    """Save cache entries to local JSON file."""
    try:
        with open(PERSISTENT_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Error saving persistent cache file: {e}")


# Initialize persistent cache on module load
load_persistent_cache()


def get_cache_key(endpoint, **kwargs):
    """Generate cache key from endpoint and parameters.
    
    Normalizes start_date/end_date to nearest minute for better cache hit rate.
    """
    kwargs = dict(kwargs)
    if kwargs.get('start_date'):
        try:
            dt = datetime.fromisoformat(str(kwargs['start_date']).replace('Z', '+00:00'))
            kwargs['start_date'] = dt.strftime('%Y-%m-%dT%H:%M')
        except (ValueError, AttributeError):
            pass
    if kwargs.get('end_date'):
        try:
            dt = datetime.fromisoformat(str(kwargs['end_date']).replace('Z', '+00:00'))
            kwargs['end_date'] = dt.strftime('%Y-%m-%dT%H:%M')
        except (ValueError, AttributeError):
            pass
    key_parts = [endpoint] + [f"{k}:{v}" for k, v in sorted(kwargs.items())]
    key_string = "|".join(key_parts)
    return hashlib.md5(key_string.encode()).hexdigest()


def get_cached(key, ttl):
    """Get cached data if not expired."""
    if key in _cache:
        data, timestamp = _cache[key]
        age = time.time() - timestamp
        if age < ttl:
            logger.debug(f"Cache HIT (age: {age:.1f}s)")
            return data
        else:
            del _cache[key]
            save_persistent_cache()
            logger.debug(f"Cache EXPIRED (age: {age:.1f}s)")
    logger.debug(f"Cache MISS")
    return None


def set_cache(key, data):
    """Store data in cache with current timestamp."""
    _cache[key] = (data, time.time())
    save_persistent_cache()
    logger.debug(f"Cache SET ({len(_cache)} entries)")


def clear_cache():
    """Clear all cache entries."""
    _cache.clear()
    save_persistent_cache()
    logger.info("Cache cleared")


@app.route('/api/clear-cache', methods=['POST'])
def clear_cache_endpoint():
    """Clear cache endpoint (for admin use)."""
    clear_cache()
    return jsonify({'success': True, 'message': 'Cache cleared'})

# Initialize PDF styles at module level
try:
    PDF_STYLES = getSampleStyleSheet()
except Exception as e:
    logger.error(f"Failed to initialize PDF styles at module level: {e}", exc_info=True)
    PDF_STYLES = None


def normalize_to_utc(dt):
    """Normalize datetime to UTC 0 (+00:00), converting from any timezone.
    
    Args:
        dt: datetime object (can be timezone-aware or naive, any timezone)
    
    Returns:
        datetime object in UTC 0 (+00:00)
    
    Examples:
        - Input: datetime(2026, 3, 18, 12, 48, 38, tzinfo=timezone(timedelta(hours=8)))  # +08:00
        - Output: datetime(2026, 3, 18, 4, 48, 38, tzinfo=timezone.utc)  # UTC 0
        
        - Input: datetime(2026, 3, 18, 12, 48, 38)  # naive
        - Output: datetime(2026, 3, 18, 12, 48, 38, tzinfo=timezone.utc)  # UTC 0 (assumed UTC)
    """
    if dt is None:
        return None
    
    if isinstance(dt, str):
        # Parse ISO string first
        dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
    
    if dt.tzinfo is None:
        # Naive datetime - assume it's already UTC and add UTC timezone
        return dt.replace(tzinfo=timezone.utc)
    else:
        # Timezone-aware datetime - convert to UTC 0
        return dt.astimezone(timezone.utc)


def _format_date_safe(dt):
    """Safely format a date (datetime or string) to ISO string.
    
    Handles both datetime objects and strings, ensuring we never call isoformat() on a string.
    """
    if dt is None:
        return None
    
    if isinstance(dt, str):
        # Already a string, try to normalize if it has timezone info
        try:
            value_str = dt.replace('Z', '+00:00')
            dt_obj = datetime.fromisoformat(value_str)
            normalized_dt = normalize_to_utc(dt_obj)
            if isinstance(normalized_dt, datetime):
                return normalized_dt.isoformat()
            else:
                return str(normalized_dt) if normalized_dt else None
        except Exception as e:
            logger.warning(f"Error parsing date string '{dt}': {e}")
            return dt  # Keep as-is if parsing fails
    elif isinstance(dt, datetime):
        normalized_dt = normalize_to_utc(dt)
        if isinstance(normalized_dt, datetime):
            return normalized_dt.isoformat()
        else:
            return str(normalized_dt) if normalized_dt else None
    else:
        # Unknown type, convert to string
        return str(dt) if dt else None


def parse_date_range(period, start_date=None, end_date=None):
    """Parse and return start/end datetime based on period or custom dates.
    
    All datetime objects are normalized to UTC 0 (+00:00) for consistency.
    This ensures that even if MongoDB stores dates with +08:00, all comparisons
    and operations use UTC 0.
    
    Matches the logic from count_import_daily.py:
    - now = datetime.now(timezone.utc)
    - For daily: start = now - timedelta(hours=24), end = now
    - Query: {"inserted_time": {"$gte": start, "$lte": end}}
    
    No limit on date range.
    """
    now = datetime.now(timezone.utc)
    
    if start_date and end_date:
        # Handle different date formats from URL
        try:
            # Parse dates and normalize to UTC 0
            if 'Z' in start_date or '+' in start_date or start_date.endswith('UTC'):
                start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            else:
                # Parse without timezone and add UTC
                start = datetime.fromisoformat(start_date)
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
            
            if 'Z' in end_date or '+' in end_date or end_date.endswith('UTC'):
                end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            else:
                # Parse without timezone and add UTC
                end = datetime.fromisoformat(end_date)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
            
            # Normalize both to UTC 0 (convert from any timezone like +08:00 to +00:00)
            start = normalize_to_utc(start)
            end = normalize_to_utc(end)
        except (ValueError, AttributeError) as e:
            logger.warning(f"Error parsing date range, using default period: {e}")
            # Fallback to default period if parsing fails
            if period == 'daily':
                start = now - timedelta(hours=24)
            else:  # weekly (default)
                start = now - timedelta(days=7)
            end = now
    else:
        # Calculate based on period - matches count_import_daily.py logic
        # For daily: now - timedelta(hours=24) to now
        # For weekly: now - timedelta(days=7) to now
        if period == 'daily':
            start = now - timedelta(hours=24)  # Same as count_import_daily.py: one_day_ago = now - timedelta(hours=24)
        else:  # weekly (default)
            start = now - timedelta(days=7)
        end = now  # Always use current time as end
    
    return start, end


_timestamps_cache = None
_timestamps_cache_time = 0
TIMESTAMPS_CACHE_TTL = 300  # 5 minutes


def get_data_timestamps():
    """Get date range information from source collections.
    
    Cached for 5 minutes - full collection scans are slow.
    """
    global _timestamps_cache, _timestamps_cache_time
    if _timestamps_cache is not None and (time.time() - _timestamps_cache_time) < TIMESTAMPS_CACHE_TTL:
        return _timestamps_cache
    try:
        # Get oldest and newest dates from source collections
        dated_info = {}
        
        # Helper function to get date range for a collection (O(1) optimized using sort & limit)
        def get_date_range(collection_name, date_field):
            try:
                coll = get_collection(collection_name)
                # Query index instead of doing full collection scan via $group
                oldest_doc = list(coll.find({date_field: {"$ne": None}}, {date_field: 1}).sort(date_field, 1).limit(1))
                newest_doc = list(coll.find({date_field: {"$ne": None}}, {date_field: 1}).sort(date_field, -1).limit(1))
                
                oldest = oldest_doc[0].get(date_field) if oldest_doc else None
                newest = newest_doc[0].get(date_field) if newest_doc else None
                
                if oldest is not None or newest is not None:
                    return {"oldest": oldest, "newest": newest}
                return None
            except Exception as e:
                logger.warning(f"Error getting date range for {collection_name}: {e}")
                return None
        
        # Get date ranges for each collection
        for coll_name, coll_config in COLLECTIONS.items():
            try:
                if coll_name == 'organizations':
                    # Special handling for organizations (O(1) optimized using sort & limit)
                    org_col = get_collection('organizations')
                    try:
                        old_c = list(org_col.find({"created_at": {"$ne": None}}, {"created_at": 1}).sort("created_at", 1).limit(1))
                        new_c = list(org_col.find({"created_at": {"$ne": None}}, {"created_at": 1}).sort("created_at", -1).limit(1))
                        old_u = list(org_col.find({"updated_at": {"$ne": None}}, {"updated_at": 1}).sort("updated_at", 1).limit(1))
                        new_u = list(org_col.find({"updated_at": {"$ne": None}}, {"updated_at": 1}).sort("updated_at", -1).limit(1))
                        
                        org_data = {
                            "oldest_created": old_c[0].get("created_at") if old_c else None,
                            "newest_created": new_c[0].get("created_at") if new_c else None,
                            "oldest_updated": old_u[0].get("updated_at") if old_u else None,
                            "newest_updated": new_u[0].get("updated_at") if new_u else None
                        }
                    except Exception as org_e:
                        logger.warning(f"Error optimizing organizations date range: {org_e}")
                        org_data = {}
                    
                    if org_data:
                        # Convert datetime fields to ISO strings, normalized to UTC 0
                        for key in ['oldest_created', 'newest_created', 'oldest_updated', 'newest_updated']:
                            if key in org_data and org_data[key] is not None:
                                try:
                                    if isinstance(org_data[key], datetime):
                                        # Normalize to UTC 0 before converting to ISO string
                                        org_data[key] = normalize_to_utc(org_data[key]).isoformat()
                                    elif isinstance(org_data[key], str):
                                        # Parse and normalize if it's a string (e.g., from MongoDB with +08:00)
                                        # Handle both 'Z' and timezone offsets like '+08:00'
                                        value_str = org_data[key].replace('Z', '+00:00')
                                        value_dt = datetime.fromisoformat(value_str)
                                        # Normalize to UTC 0
                                        normalized_dt = normalize_to_utc(value_dt)
                                        # Ensure output is in UTC 0 format (+00:00)
                                        org_data[key] = normalized_dt.isoformat()
                                except Exception as e:
                                    logger.warning(f"Error normalizing {key} for organizations: {e}")
                                    # Keep as-is if parsing fails
                        org_data["field"] = "created_at/updated_at"
                        dated_info["organizations"] = org_data
                else:
                    date_field = coll_config.get('date_field')
                    if date_field:
                        date_range = get_date_range(coll_name, date_field)
                        if date_range:
                            oldest = date_range.get("oldest")
                            newest = date_range.get("newest")
                            # Normalize datetime to UTC 0 before converting to ISO string
                            if isinstance(oldest, datetime):
                                oldest = normalize_to_utc(oldest).isoformat()
                            elif isinstance(oldest, str):
                                # Parse and normalize if it's a string (e.g., from MongoDB with +08:00)
                                try:
                                    oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                                    oldest = normalize_to_utc(oldest_dt).isoformat()
                                except:
                                    pass  # Keep as-is if parsing fails
                            
                            if isinstance(newest, datetime):
                                newest = normalize_to_utc(newest).isoformat()
                            elif isinstance(newest, str):
                                # Parse and normalize if it's a string (e.g., from MongoDB with +08:00)
                                try:
                                    newest_dt = datetime.fromisoformat(newest.replace('Z', '+00:00'))
                                    newest = normalize_to_utc(newest_dt).isoformat()
                                except:
                                    pass  # Keep as-is if parsing fails
                            dated_info[coll_name] = {
                                "oldest": oldest,
                                "newest": newest,
                                "field": date_field
                            }
            except Exception as coll_error:
                logger.warning(f"Error processing collection {coll_name}: {coll_error}")
        
        result = {"dated": dated_info}
        _timestamps_cache = result
        _timestamps_cache_time = time.time()
        return result
    except Exception as e:
        logger.error(f"Error getting data timestamps: {e}", exc_info=True)
        return {"dated": {}}


def get_organizations_stats():
    """Get organizations and domains statistics - optimized with combined pipeline."""
    try:
        org_col = get_collection('organizations')
        
        # Count indexes (lightweight operation)
        try:
            indexes = list(org_col.list_indexes())
            index_count = len(indexes)
        except Exception as e:
            logger.warning(f"Error counting indexes: {e}")
            index_count = 0
        
        # Combined pipeline for organizations stats and unique domains
        combined_pipeline = [
            {
                "$project": {
                    "domain_count": {
                        "$cond": {
                            "if": {"$isArray": "$domains"},
                            "then": {"$size": "$domains"},
                            "else": 0
                        }
                    },
                    "domains": 1
                }
            },
            {
                "$facet": {
                    "org_stats": [
                        {
                            "$group": {
                                "_id": None,
                                "total_organizations": {"$sum": 1},
                                "total_domains": {"$sum": "$domain_count"},
                                "organizations_with_domains": {
                                    "$sum": {
                                        "$cond": [{"$gt": ["$domain_count", 0]}, 1, 0]
                                    }
                                }
                            }
                        }
                    ],
                    "unique_domains": [
                        {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
                        {"$match": {"domains": {"$ne": None, "$ne": ""}}},
                        {"$group": {"_id": "$domains"}},
                        {"$count": "unique_domains"}
                    ],
                    "domain_occurrences": [
                        {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
                        {"$match": {"domains": {"$ne": None, "$ne": ""}}},
                        {"$group": {"_id": "$domains", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": 20}  # Top 20 most frequent domains
                    ]
                }
            }
        ]
        
        result = list(org_col.aggregate(combined_pipeline, allowDiskUse=True))
        
        if result and result[0]:
            facets = result[0]
            
            # Extract organization stats
            org_stats = facets.get('org_stats', [{}])[0] if facets.get('org_stats') else {}
            total_orgs = org_stats.get("total_organizations", 0)
            total_domains = org_stats.get("total_domains", 0)
            orgs_with_domains = org_stats.get("organizations_with_domains", 0)
            
            # Extract unique domains count
            unique_result = facets.get('unique_domains', [{}])[0] if facets.get('unique_domains') else {}
            unique_domains = unique_result.get("unique_domains", 0)
            
            # Extract domain occurrences
            domain_occurrences_list = facets.get('domain_occurrences', [])
            domain_occurrences = {}
            for item in domain_occurrences_list:
                domain = item.get("_id")
                count = item.get("count", 0)
                if domain:  # Only include non-empty domains
                    domain_occurrences[domain] = count
            
            logger.info(f"Found {len(domain_occurrences)} unique domains in occurrences")
        else:
            total_orgs = 0
            total_domains = 0
            orgs_with_domains = 0
            unique_domains = 0
            domain_occurrences = {}
        
        return {
            "organizations_indexes": index_count,
            "total_organizations": total_orgs,
            "total_domains": total_domains,
            "unique_domains": unique_domains,
            "organizations_with_domains": orgs_with_domains,
            "domain_occurrences": domain_occurrences
        }
    except Exception as e:
        logger.error(f"Error getting organizations stats: {e}", exc_info=True)
        return {
            "organizations_indexes": 0,
            "total_organizations": 0,
            "total_domains": 0,
            "unique_domains": 0,
            "organizations_with_domains": 0,
            "domain_occurrences": {}
        }


def get_stats_from_db(start, end, metrics=None):
    """Get statistics from MongoDB source collections.
    
    Args:
        start: Start datetime for date-filtered stats (UTC 0)
        end: End datetime for date-filtered stats (UTC 0)
        metrics: Optional list of metrics/metric groups to query
    
    Returns:
        dict: Combined statistics including date-filtered and organization stats
    """
    try:
        # Ensure start and end are UTC 0 datetime objects
        start = normalize_to_utc(start) if start else datetime.now(timezone.utc) - timedelta(hours=24)
        end = normalize_to_utc(end) if end else datetime.now(timezone.utc)
        
        # Log query parameters for debugging
        logger.info(f"Querying stats ({metrics}) from {start.isoformat()} to {end.isoformat()} (UTC 0)")
        
        # Execute queries from source collections
        stats = execute_stats_queries(start, end, metrics=metrics)
        
        # Add organizations stats (not date-dependent)
        if metrics is None or 'fast' in metrics:
            org_stats = get_organizations_stats()
            if org_stats:
                stats.update(org_stats)
        
        # Log results for debugging
        logger.info(f"Stats results: zip_import={stats.get('zip_import', 0)}, decompressed={stats.get('decompressed', 0)}")
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting stats from DB: {e}", exc_info=True)
        # Return empty stats on error to prevent frontend crashes
        return {
            'zip_import': 0,
            'decompressed': 0,
            'credentials': 0,
            'hwid': 0,
            'total_organizations': 0,
            'organizations_indexes': 0,
            'total_domains': 0,
            'unique_domains': 0,
            'organizations_with_domains': 0,
            'domain_occurrences': {},
            'credential_types': {},
            'alerts_by_org': {},
            'top_domains_alerts': {},
            'dated': {}
        }


def get_chart_intervals(start, end, period):
    """Calculate chart intervals based on date range.
    
    Optimized to reduce number of intervals for better performance.
    Credentials queries are very slow, so reducing intervals significantly improves performance.
    """
    total_seconds = (end - start).total_seconds()
    
    if period == 'daily' or total_seconds <= 86400:
        # Daily: 6 intervals (4-hour) for faster load - credentials query is the bottleneck
        intervals = min(6, max(1, int(total_seconds / 14400)))  # 14400 seconds = 4 hours
        delta = timedelta(seconds=total_seconds / intervals)
    else:
        # Weekly: 7 intervals (daily) for better performance
        # This reduces from potentially 168 intervals (hourly) to just 7
        intervals = min(7, max(1, int(total_seconds / 86400)))
        delta = timedelta(seconds=total_seconds / intervals)
    
    return intervals, delta


def get_chart_data_optimized(start, end, intervals, delta, period='weekly'):
    """Get chart data using optimized aggregation pipeline.
    
    Optimized to use facet for combining multiple queries and better error handling.
    
    Args:
        start: Start datetime
        end: End datetime
        intervals: Number of intervals
        delta: Time delta between intervals
        period: Period type ('daily' or 'weekly')
    
    Performance optimizations:
    - Always uses range queries ($gte and $lte) for credentials to avoid timeout
    - Uses maxTimeMS=10000 for all queries to prevent hanging
    - Skips intervals on timeout instead of failing entire request
    """
    labels = []
    try:
        archives_col = get_collection('archives')
        credentials_col = get_collection('credentials')
        alerts_col = get_collection('alerts')
        
        credentials_timeout_count = [0]  # Use list for mutable access in nested closure
        
        datasets = {
            'zip_import': [0] * intervals,
            'decompressed': [0] * intervals,
            'credentials': [0] * intervals,
            'hwid': [0] * intervals
        }
        
        # Generate labels
        label_format = '%H:00' if delta.total_seconds() < 86400 else '%m/%d'
        for i in range(intervals):
            interval_start = start + delta * i
            labels.append(interval_start.strftime(label_format))
        
        # CRITICAL FIX: Always use range queries for credentials to avoid timeout
        # Using $gte only causes each interval to count ALL credentials from start, causing timeout
        # Range queries ($gte and $lte) are much faster and prevent duplicate counting
        use_range_for_credentials = True  # Always use range for performance
        
        # Build pipelines and execute queries - parallelize 4 queries per interval
        from db_helpers import build_hwid_pipeline
        def _query_interval(args):
            i, interval_start, interval_end = args
            zip_p = build_count_pipeline('inserted_time', interval_start, interval_end, None, use_gte_only=True)
            decomp_p = build_count_pipeline('inserted_time', interval_start, interval_end, {'is_decompressed': True})
            creds_p = build_count_pipeline('harvest_date', interval_start, interval_end, None, use_gte_only=False)
            hwid_p = build_hwid_pipeline(interval_start, interval_end)
            results = {}
            try:
                r = list(archives_col.aggregate(zip_p, allowDiskUse=True))
                results['zip'] = r[0]['total'] if r else 0
            except Exception:
                results['zip'] = 0
            try:
                r = list(archives_col.aggregate(decomp_p, allowDiskUse=True))
                results['decomp'] = r[0]['total'] if r else 0
            except Exception:
                results['decomp'] = 0
            try:
                r = list(credentials_col.aggregate(creds_p, allowDiskUse=True))
                results['creds'] = r[0]['total'] if r else 0
            except Exception as e:
                if 'timed out' in str(e).lower() or 'timeout' in str(e).lower():
                    credentials_timeout_count[0] += 1
                results['creds'] = 0
            try:
                r = list(alerts_col.aggregate(hwid_p, allowDiskUse=True))
                results['hwid'] = r[0]['total'] if r else 0
            except Exception:
                results['hwid'] = 0
            return (i, results)
        
        interval_args = [(i, start + delta * i, start + delta * (i + 1)) for i in range(intervals)]
        with ThreadPoolExecutor(max_workers=min(intervals, 8)) as executor:
             futures = {executor.submit(_query_interval, args): args[0] for args in interval_args}
             for future in as_completed(futures):
                 try:
                     i, results = future.result()
                     datasets['zip_import'][i] = results['zip']
                     datasets['decompressed'][i] = results['decomp']
                     datasets['credentials'][i] = results['creds']
                     datasets['hwid'][i] = results['hwid']
                 except Exception as e:
                     logger.warning(f"Error processing interval: {e}")
        
        if credentials_timeout_count[0] > 0:
            logger.warning(f"Credentials query had {credentials_timeout_count[0]}/{intervals} timeouts")
        
        return labels, datasets
    except Exception as e:
        logger.error(f"Error getting chart data: {e}", exc_info=True)
        # Return empty data on error with proper structure
        return labels if labels else [f"Interval {i+1}" for i in range(intervals)], {
            'zip_import': [0] * intervals,
            'decompressed': [0] * intervals,
            'credentials': [0] * intervals,
            'hwid': [0] * intervals
        }


@app.route('/')
def index():
    """Render dashboard page."""
    logger.info("Serving dashboard page")
    return render_template('dashboard.html')


@app.route('/api/stats')
def get_stats():
    """Get statistics API endpoint - supports lazy loading via metric query parameter.
    
    Uses cache with 1 hour TTL for better performance.
    """
    try:
        period = request.args.get('period', 'daily')  # Changed default to 'daily' to match frontend
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        metric = request.args.get('metric', 'all')  # all, fast, credentials, hwid
        
        # Check cache first
        cache_key = get_cache_key('stats', period=period, start_date=start_date, end_date=end_date)
        cached_result = get_cached(cache_key, CACHE_TTL_STATS)
        
        # If cache exists and we have the requested metric, return it immediately
        if cached_result is not None and isinstance(cached_result, dict) and 'stats' in cached_result:
            stats = cached_result['stats']
            has_metric = False
            if metric == 'all':
                has_metric = True
            elif metric == 'fast':
                has_metric = stats.get('zip_import', 0) > 0 or stats.get('total_organizations', 0) > 0
            elif metric == 'credentials':
                has_metric = stats.get('credentials', 0) > 0 or len(stats.get('credential_types', {})) > 0
            elif metric == 'hwid':
                has_metric = stats.get('hwid', 0) > 0
                
            if has_metric:
                logger.info(f"API /api/stats - returning cached result for metric {metric}")
                return jsonify(cached_result)
        
        logger.info(f"API /api/stats endpoint called for metric {metric}")
        start, end = parse_date_range(period, start_date, end_date)
        
        # Log query parameters with detailed info
        logger.info(f"API /api/stats called - period={period}, start_date={start_date}, end_date={end_date}, metric={metric}")
        logger.info(f"API /api/stats parsed dates - start={start.isoformat()}, end={end.isoformat()}")
        
        # Query only the requested metric from database
        db_metrics = None if metric == 'all' else [metric]
        stats = get_stats_from_db(start, end, metrics=db_metrics)
        
        # If fast stats are requested, also retrieve data timestamps
        if metric == 'all' or metric == 'fast':
            timestamps = get_data_timestamps()
            stats["dated"] = timestamps.get("dated", {})
            
        # Merge stats into existing cache if present
        if cached_result is not None and isinstance(cached_result, dict) and 'stats' in cached_result:
            existing_stats = cached_result['stats']
            for k, v in stats.items():
                if isinstance(v, dict) and k in existing_stats and isinstance(existing_stats[k], dict):
                    existing_stats[k].update(v)
                else:
                    existing_stats[k] = v
            # Use the merged cached result
            result = cached_result
            result['period'] = period
            result['start_date'] = start.isoformat()
            result['end_date'] = end.isoformat()
        else:
            # Initialize a new statistics dictionary with all fields defaulted to prevent frontend errors
            default_stats = {
                'zip_import': 0,
                'decompressed': 0,
                'credentials': 0,
                'hwid': 0,
                'total_organizations': 0,
                'organizations_indexes': 0,
                'total_domains': 0,
                'unique_domains': 0,
                'organizations_with_domains': 0,
                'domain_occurrences': {},
                'credential_types': {},
                'alerts_by_org': {},
                'top_domains_alerts': {},
                'dated': {}
            }
            default_stats.update(stats)
            result = {
                'success': True,
                'stats': default_stats,
                'period': period,
                'start_date': start.isoformat(),
                'end_date': end.isoformat()
            }
            
        # Cache the merged or new result under the common cache key
        set_cache(cache_key, result)
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /api/stats: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/chart-data')
def get_chart_data():
    """Get chart data API endpoint - always queries from source collections.
    
    Optimized for performance:
    - Weekly period uses daily intervals (7 intervals instead of 168)
    - Credentials query uses range ($gte and $lte) for weekly to avoid duplicate counting
    - Uses cache with 5 minute TTL for better performance
    """
    try:
        period = request.args.get('period', 'daily')  # Changed default to 'daily' to match frontend
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Check cache first
        cache_key = get_cache_key('chart-data', period=period, start_date=start_date, end_date=end_date)
        cached_result = get_cached(cache_key, CACHE_TTL_CHART)
        if cached_result is not None:
            logger.info(f"API /api/chart-data - returning cached result")
            return jsonify(cached_result)
        
        logger.info(f"API /api/chart-data endpoint called")
        start, end = parse_date_range(period, start_date, end_date)
        
        # Log query parameters with detailed info
        logger.info(f"API /api/chart-data called - period={period}, start_date={start_date}, end_date={end_date}")
        logger.info(f"API /api/chart-data parsed dates - start={start.isoformat()}, end={end.isoformat()}")
        
        # Query from source collections
        intervals, delta = get_chart_intervals(start, end, period)
        logger.info(f"Chart intervals: {intervals}, delta: {delta.total_seconds()} seconds")
        
        labels, datasets = get_chart_data_optimized(start, end, intervals, delta, period)
        
        # Log chart data summary
        total_zip = sum(datasets.get('zip_import', []))
        total_decompressed = sum(datasets.get('decompressed', []))
        total_credentials = sum(datasets.get('credentials', []))
        total_hwid = sum(datasets.get('hwid', []))
        logger.info(f"API /api/chart-data response - total_zip={total_zip}, "
                   f"total_decompressed={total_decompressed}, "
                   f"total_credentials={total_credentials}, "
                   f"total_hwid={total_hwid}")
        
        result = {
            'success': True,
            'labels': labels,
            'datasets': datasets
        }
        
        # Cache the result
        set_cache(cache_key, result)
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in /api/chart-data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export-csv')
def export_csv():
    """Export statistics as CSV with comprehensive data."""
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Check cache first (reuse the same stats cache)
        cache_key = get_cache_key('stats', period=period, start_date=start_date, end_date=end_date)
        cached_result = get_cached(cache_key, CACHE_TTL_STATS)
        if cached_result is not None and isinstance(cached_result, dict) and 'stats' in cached_result:
            logger.info("CSV export - Cache HIT for stats data")
            stats = cached_result['stats']
        else:
            logger.info("CSV export - Cache MISS for stats data, querying database")
            stats = get_stats_from_db(start, end)
        
        # Ensure stats is a dict and has required keys
        if not isinstance(stats, dict):
            stats = {}
        
        # Get dated info
        timestamps = get_data_timestamps()
        
        # Build comprehensive CSV
        csv_lines = []
        
        # Header section
        csv_lines.append("Breachunt - MongoDB Statistics Report")
        csv_lines.append(f"Report Period: {period.upper()}")
        csv_lines.append(f"Date Range: {start.strftime('%Y-%m-%d %H:%M:%S')} to {end.strftime('%Y-%m-%d %H:%M:%S')}")
        csv_lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        csv_lines.append("")
        
        # Main statistics
        csv_lines.append("MAIN STATISTICS")
        csv_lines.append("Metric,Count")
        csv_lines.append(f"Zip Archives Imported,{int(stats.get('zip_import', 0)):,}")
        csv_lines.append(f"Decompressed Archives,{int(stats.get('decompressed', 0)):,}")
        csv_lines.append(f"Credentials Found,{int(stats.get('credentials', 0)):,}")
        csv_lines.append(f"HWID Found,{int(stats.get('hwid', 0)):,}")
        csv_lines.append("")
        
        # Organizations statistics
        csv_lines.append("ORGANIZATIONS STATISTICS")
        csv_lines.append("Metric,Count")
        csv_lines.append(f"Total Organizations,{int(stats.get('total_organizations', 0)):,}")
        csv_lines.append(f"Organization Indexes,{int(stats.get('organizations_indexes', 0)):,}")
        csv_lines.append(f"Total Domains,{int(stats.get('total_domains', 0)):,}")
        csv_lines.append(f"Unique Domains,{int(stats.get('unique_domains', 0)):,}")
        csv_lines.append(f"Organizations with Domains,{int(stats.get('organizations_with_domains', 0)):,}")
        csv_lines.append("")
        
        # Domain occurrences (top 20)
        if stats.get('domain_occurrences'):
            csv_lines.append("TOP DOMAIN OCCURRENCES")
            csv_lines.append("Domain,Count")
            sorted_domains = sorted(
                stats['domain_occurrences'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:20]
            for domain, count in sorted_domains:
                csv_lines.append(f"{domain},{int(count):,}")
            csv_lines.append("")
        
        # Data date ranges
        if timestamps and timestamps.get('dated'):
            csv_lines.append("DATA DATE RANGES")
            csv_lines.append("Collection,Field,Oldest Date,Newest Date")
            for coll, info in timestamps['dated'].items():
                if not isinstance(info, dict):
                    continue
                field = info.get('field', 'N/A')
                oldest = info.get('oldest') or info.get('oldest_created') or 'N/A'
                newest = info.get('newest') or info.get('newest_created') or 'N/A'
                try:
                    oldest_str = 'N/A'
                    newest_str = 'N/A'
                    
                    if oldest != 'N/A' and oldest:
                        if isinstance(oldest, str):
                            oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                        elif isinstance(oldest, datetime):
                            oldest_dt = oldest
                        else:
                            oldest_dt = None
                        
                        if oldest_dt:
                            # Normalize to UTC 0 before formatting
                            oldest_dt = normalize_to_utc(oldest_dt)
                            oldest_str = oldest_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    
                    if newest != 'N/A' and newest:
                        if isinstance(newest, str):
                            newest_dt = datetime.fromisoformat(newest.replace('Z', '+00:00'))
                        elif isinstance(newest, datetime):
                            newest_dt = newest
                        else:
                            newest_dt = None
                        
                        if newest_dt:
                            # Normalize to UTC 0 before formatting
                            newest_dt = normalize_to_utc(newest_dt)
                            newest_str = newest_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    
                    csv_lines.append(f"{coll},{field},{oldest_str},{newest_str}")
                except (ValueError, AttributeError, TypeError) as e:
                    logger.warning(f"Error formatting date for {coll}: {e}")
                    csv_lines.append(f"{coll},{field},N/A,N/A")
        
        filename = f"mongodb_report_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        
        return Response(
            '\n'.join(csv_lines),
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'text/csv; charset=utf-8'
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _generate_pdf_report(period, start_date, end_date):
    """Core logic to generate the comprehensive professional PDF report matching the sample layout."""
    global PDF_STYLES
    
    # Initialize PDF styles
    styles = PDF_STYLES if PDF_STYLES is not None else None
    if styles is None:
        styles = getSampleStyleSheet()
        PDF_STYLES = styles
        
    start, end = parse_date_range(period, start_date, end_date)
    
    # Ensure UTC 0
    start = normalize_to_utc(start)
    end = normalize_to_utc(end)
    
    # Check cache first
    cache_key = get_cache_key('stats', period=period, start_date=start_date, end_date=end_date)
    cached_result = get_cached(cache_key, CACHE_TTL_STATS)
    if cached_result is not None and isinstance(cached_result, dict) and 'stats' in cached_result:
        stats = cached_result['stats']
    else:
        stats = get_stats_from_db(start, end)
        
    if not isinstance(stats, dict):
        stats = {}
        
    # Format dates
    period_text = "Daily Report (24 hours)" if period == 'daily' else "Weekly Report (7 days)"
    date_range = f"{start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}"
    
    # Breachunt brand colors
    cosigma_cyan = colors.HexColor('#06b6d4')
    cosigma_blue = colors.HexColor('#0ea5e9')
    cosigma_purple = colors.HexColor('#a78bfa')
    cosigma_orange = colors.HexColor('#fb923c')
    cosigma_gray = colors.HexColor('#94a3b8')
    cosigma_dark = colors.HexColor('#1e293b')
    cosigma_light_gray = colors.HexColor('#f8fafc')
    
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
    logo_exists = os.path.exists(logo_path)
    
    buffer = BytesIO()
    
    # Total pages callback and page tracker
    current_page_var = [0]
    
    def add_cover_page(canvas_obj, doc_obj):
        canvas_obj.saveState()
        page_width, page_height = A4
        
        # Top header color block
        canvas_obj.setFillColor(cosigma_cyan)
        header_height = page_height * 0.30
        canvas_obj.rect(0, page_height - header_height, page_width, header_height, fill=1, stroke=0)
        
        # Logo
        logo_size = 1.4*inch
        logo_x = (page_width - logo_size) / 2
        logo_y = page_height - header_height + (header_height - logo_size) / 2 - 0.1*inch
        if logo_exists:
            try:
                canvas_obj.drawImage(logo_path, logo_x, logo_y, width=logo_size, height=logo_size, preserveAspectRatio=True)
            except:
                pass
        
        # Company Name
        canvas_obj.setFont('Times-Roman', 34)
        canvas_obj.setFillColor(colors.white)
        canvas_obj.drawCentredString(page_width / 2, logo_y - 0.7*inch, 'Breachunt')
        
        # Main Title
        content_start_y = page_height - header_height - 1.8*inch
        canvas_obj.setFont('Helvetica-Bold', 32)
        canvas_obj.setFillColor(cosigma_dark)
        canvas_obj.drawCentredString(page_width / 2, content_start_y, 'MongoDB Statistics Report')
        
        # Period
        canvas_obj.setFont('Helvetica', 20)
        canvas_obj.setFillColor(cosigma_blue)
        canvas_obj.drawCentredString(page_width / 2, content_start_y - 0.7*inch, period_text)
        
        # Divider
        canvas_obj.setStrokeColor(cosigma_cyan)
        canvas_obj.setLineWidth(2.5)
        canvas_obj.line(1.0*inch, content_start_y - 1.25*inch, page_width - 1.0*inch, content_start_y - 1.25*inch)
        
        # Date Range
        canvas_obj.setFont('Helvetica', 14)
        canvas_obj.setFillColor(cosigma_gray)
        canvas_obj.drawCentredString(page_width / 2, content_start_y - 1.7*inch, date_range)
        
        # Generated Timestamp
        bottom_section_y = 3.2*inch
        canvas_obj.setFont('Helvetica', 11)
        canvas_obj.setFillColor(cosigma_gray)
        canvas_obj.drawCentredString(page_width / 2, bottom_section_y, f'Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}')
        
        # Footer Company info
        footer_y = 1.1*inch
        canvas_obj.setStrokeColor(cosigma_cyan)
        canvas_obj.setLineWidth(1.2)
        canvas_obj.line(1.0*inch, footer_y + 0.6*inch, page_width - 1.0*inch, footer_y + 0.6*inch)
        canvas_obj.setFont('Helvetica', 10)
        canvas_obj.setFillColor(cosigma_dark)
        canvas_obj.drawCentredString(page_width / 2, footer_y, 'SIRET: 952 164 911 00010')
        canvas_obj.drawCentredString(page_width / 2, footer_y - 0.3*inch, '3 terrasse Valmy, 92800 PUTEAUX, France')
        
        canvas_obj.restoreState()
        current_page_var[0] += 1
        
    def add_header_footer(canvas_obj, doc_obj):
        canvas_obj.saveState()
        page_width, page_height = A4
        current_page_var[0] += 1
        
        if current_page_var[0] == 1:
            canvas_obj.restoreState()
            return
            
        # Header - BREACHUNT logo and text
        logo_x = 0.5*inch
        logo_y = page_height - 0.5*inch - 0.6*inch
        logo_size = 0.7*inch
        if logo_exists:
            try:
                canvas_obj.drawImage(logo_path, logo_x, logo_y, width=logo_size, height=logo_size, preserveAspectRatio=True)
            except:
                pass
        text_x = logo_x + logo_size + 0.15*inch
        canvas_obj.setFont('Times-Roman', 22)
        canvas_obj.setFillColor(colors.black)
        canvas_obj.drawString(text_x, logo_y + logo_size / 2 - 0.15*inch, 'BREACHUNT')
        
        # Footer - Page Number and Company info
        footer_y = 0.4*inch
        canvas_obj.setFont('Helvetica', 8)
        canvas_obj.setFillColor(cosigma_gray)
        page_num = current_page_var[0] - 1
        canvas_obj.drawRightString(page_width - 0.5*inch, footer_y + 0.2*inch, f'Page {page_num}')
        canvas_obj.restoreState()
        
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.6*inch,
        leftMargin=0.6*inch,
        topMargin=1.2*inch,
        bottomMargin=1.0*inch
    )
    elements = []
    
    # Styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=cosigma_cyan,
        spaceAfter=12,
        alignment=TA_LEFT,
        fontName='Helvetica-Bold',
        leading=32
    )
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=cosigma_cyan,
        spaceAfter=14,
        spaceBefore=20,
        fontName='Helvetica-Bold',
        leading=20
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['Normal'],
        fontSize=11,
        textColor=cosigma_dark,
        spaceAfter=14,
        alignment=TA_LEFT,
        fontName='Helvetica',
        leading=17
    )
    
    # 1. Page Break to Page 2 (TOC)
    elements.append(PageBreak())
    
    toc_title_style = ParagraphStyle('TOCTitle', parent=title_style, fontSize=26, alignment=TA_CENTER, spaceAfter=40)
    elements.append(Spacer(1, 0.4*inch))
    elements.append(Paragraph("Table of Contents", toc_title_style))
    elements.append(Spacer(1, 0.5*inch))
    
    toc_items = [
        ("Executive Summary", 3),
        ("Main Statistics", 4),
        ("Telegram Sources", 4),
        ("Selected Organization URLs", 5),
        ("Statistics Overview Chart", 8),
        ("Organizations Statistics", 10),
        ("Top Domain Occurrences", 12),
        ("Domain Occurrences Distribution", 13)
    ]
    
    toc_table_data = []
    for item, page_num in toc_items:
        toc_table_data.append([
            Paragraph(item, body_style),
            Paragraph(str(page_num), ParagraphStyle('TOCPage', parent=body_style, alignment=TA_RIGHT))
        ])
    toc_table = Table(toc_table_data, colWidths=[5.3*inch, 1.2*inch])
    toc_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(toc_table)
    
    # 2. Page Break to Page 3 (Executive Summary)
    elements.append(PageBreak())
    elements.append(Paragraph("Executive Summary", title_style))
    intro_text = f"This {period_text.lower()} provides a comprehensive overview of MongoDB statistics for the period from <b>{start.strftime('%Y-%m-%d %H:%M UTC')}</b> to <b>{end.strftime('%Y-%m-%d %H:%M UTC')}</b>."
    elements.append(Paragraph(intro_text, body_style))
    elements.append(Spacer(1, 0.2*inch))
    
    zip_count = int(stats.get('zip_import', 0))
    decomp_count = int(stats.get('decompressed', 0))
    cred_count = int(stats.get('credentials', 0))
    hwid_count = int(stats.get('hwid', 0))
    org_count = int(stats.get('total_organizations', 0))
    domain_count = int(stats.get('total_domains', 0))
    
    cred_types = stats.get('credential_types', {})
    telegram_count = int(cred_types.get('telegram', 0))
    telegram_ulp_count = int(cred_types.get('telegram_ulp', 0))
    
    exec_table_data = [
        ['Metric', 'Count'],
        ['Import Zip Archives', f"{zip_count:,}"],
        ['Decompressed Archives', f"{decomp_count:,}"],
        ['Credentials Found', f"{cred_count:,}"],
        ['HWID Identified', f"{hwid_count:,}"],
        ['Telegram (source.type)', f"{telegram_count:,}"],
        ['Telegram ULP (source.type)', f"{telegram_ulp_count:,}"],
        ['Organizations Tracked', f"{org_count:,}"],
        ['Total Domains', f"{domain_count:,}"]
    ]
    exec_table = Table(exec_table_data, colWidths=[4*inch, 2.5*inch])
    exec_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), cosigma_cyan),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    elements.append(exec_table)
    elements.append(Spacer(1, 0.2*inch))
    elements.append(Paragraph("This report contains detailed statistics, organizational insights, and domain occurrence analysis to support data-driven decision making.", body_style))
    
    # 3. Page Break to Page 4 (Main Statistics & Telegram Sources)
    elements.append(PageBreak())
    elements.append(Paragraph("Main Statistics", title_style))
    main_stats_data = [
        ['Metric', 'Count'],
        ['Import Zip Archives', f"{zip_count:,}"],
        ['Decompressed Archives', f"{decomp_count:,}"],
        ['Credentials Found', f"{cred_count:,}"],
        ['HWID Found', f"{hwid_count:,}"],
        ['Telegram (source.type)', f"{telegram_count:,}"],
        ['Telegram ULP (source.type)', f"{telegram_ulp_count:,}"]
    ]
    main_table = Table(main_stats_data, colWidths=[4*inch, 2.5*inch])
    main_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), cosigma_cyan),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    elements.append(main_table)
    elements.append(Spacer(1, 0.3*inch))
    
    elements.append(Paragraph("Telegram Sources", heading_style))
    telegram_data = [
        ['source.type', 'Count'],
        ['telegram', f"{telegram_count:,}"],
        ['telegram_ulp', f"{telegram_ulp_count:,}"]
    ]
    telegram_table = Table(telegram_data, colWidths=[4*inch, 2.5*inch])
    telegram_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), cosigma_cyan),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING', (0,0), (-1,-1), 12),
    ]))
    elements.append(telegram_table)
    
    # 4. Selected Organization URLs Section (Pages 5, 6, 7)
    targeted_orgs = ['chanel', 'chantiersatlantique', 'orano_group', 'unistra']
    alerts_col = get_collection('alerts')
    
    for idx, org in enumerate(targeted_orgs):
        org_query = {
            "organization_id": org,
            "updated_date": {"$gte": start, "$lte": end}
        }
        total_alerts_count = alerts_col.count_documents(org_query)
        
        # Prioritize alerts that have a valid HWID in the date range first
        hwid_org_query = {
            "organization_id": org,
            "updated_date": {"$gte": start, "$lte": end},
            "detections": {
                "$elemMatch": {
                    "detection_date": {"$gte": start, "$lte": end},
                    "$or": [
                        {"host.id": {"$exists": True, "$nin": [None, ""]}},
                        {"source.host.id": {"$exists": True, "$nin": [None, ""]}}
                    ]
                }
            }
        }
        hwid_alerts = list(alerts_col.find(hwid_org_query).limit(25))
        
        # If we have less than 25 HWID alerts, fetch remaining non-HWID alerts to fill the table
        remaining_limit = 25 - len(hwid_alerts)
        if remaining_limit > 0:
            non_hwid_org_query = {
                "organization_id": org,
                "updated_date": {"$gte": start, "$lte": end},
                "_id": {"$nin": [a["_id"] for a in hwid_alerts]}
            }
            non_hwid_alerts = list(alerts_col.find(non_hwid_org_query).limit(remaining_limit))
            org_alerts = hwid_alerts + non_hwid_alerts
        else:
            org_alerts = hwid_alerts
        
        
        if idx == 0:
            elements.append(PageBreak())
            elements.append(Paragraph("Selected Organization URLs", title_style))
            
        org_title_style = ParagraphStyle('OrgTitle', parent=heading_style, fontSize=14, spaceBefore=10, spaceAfter=8)
        elements.append(Paragraph(org, org_title_style))
        
        processed_alerts = []
        for alert in org_alerts:
            url = alert.get('full_url') or alert.get('normalized_url') or 'N/A'
            url_para = Paragraph(url[:65] + ('...' if len(url) > 65 else ''), ParagraphStyle('URLWrap', parent=styles['Normal'], fontSize=8, leading=10))
            login = alert.get('login') or 'N/A'
            login_para = Paragraph(login[:35] + ('...' if len(login) > 35 else ''), ParagraphStyle('LoginWrap', parent=styles['Normal'], fontSize=8, leading=10))
            
            metadata = 'no'
            hwid = 'no'
            detections = alert.get('detections', [])
            if isinstance(detections, list) and len(detections) > 0:
                valid_detections = []
                for d in detections:
                    d_date = d.get('detection_date')
                    if d_date:
                        try:
                            normalized_d_date = normalize_to_utc(d_date)
                            if start <= normalized_d_date <= end:
                                valid_detections.append(d)
                        except Exception:
                            pass
                
                if len(valid_detections) > 0:
                    metadata = 'yes'
                    for d in valid_detections:
                        host_id = d.get('host', {}).get('id') if isinstance(d.get('host'), dict) else None
                        if not host_id:
                            host_id = d.get('source', {}).get('host', {}).get('id') if isinstance(d.get('source'), dict) and isinstance(d.get('source').get('host'), dict) else None
                        if host_id:
                            hwid = host_id
                            break
            
            processed_alerts.append({
                'url_para': url_para,
                'login_para': login_para,
                'metadata': metadata,
                'hwid': hwid
            })
            
        # Prioritize alerts with valid HWID (hwid != 'no') first
        processed_alerts.sort(key=lambda x: 0 if x['hwid'] != 'no' else 1)
        
        table_header = ['URL', 'Login', 'Metadata', 'HWID']
        rows = [table_header]
        for item in processed_alerts:
            hwid_val = item['hwid']
            hwid_para = Paragraph(hwid_val[:15] + ('...' if len(hwid_val) > 15 else ''), ParagraphStyle('HWIDWrap', parent=styles['Normal'], fontSize=8, leading=10))
            rows.append([item['url_para'], item['login_para'], item['metadata'], hwid_para])
            
        if len(rows) > 1:
            org_table = Table(rows, colWidths=[3.2*inch, 1.8*inch, 0.7*inch, 1.3*inch])
            org_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), cosigma_cyan),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('TOPPADDING', (0,0), (-1,-1), 4),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ]))
            elements.append(org_table)
            
            if total_alerts_count > 25:
                elements.append(Paragraph(f"Showing first 25 of {total_alerts_count} rows for {org}.", ParagraphStyle('AlertCountFooter', parent=body_style, fontSize=8, spaceBefore=4)))
        else:
            elements.append(Paragraph("No alerts found for this organization in the selected period.", ParagraphStyle('NoAlerts', parent=body_style, fontSize=9, fontName='Helvetica-Oblique')))
            
        elements.append(Spacer(1, 0.15*inch))
        
        if org == 'chanel':
            elements.append(PageBreak())
        elif org == 'orano_group':
            elements.append(PageBreak())
            
    # 5. Page Break to Page 8 (Interval Charts Page 1)
    elements.append(PageBreak())
    pdf_intervals = 3 if period == 'daily' else 7
    pdf_delta = timedelta(hours=8) if period == 'daily' else timedelta(days=1)
    labels, datasets = get_chart_data_optimized(start, end, pdf_intervals, pdf_delta, period)
    
    zip_values = datasets.get('zip_import', [0]*pdf_intervals)
    decomp_values = datasets.get('decompressed', [0]*pdf_intervals)
    cred_values = datasets.get('credentials', [0]*pdf_intervals)
    hwid_values = datasets.get('hwid', [0]*pdf_intervals)
    
    def create_interval_chart(title, label_names, dataset, color):
        drawing = Drawing(7*inch, 2.2*inch)
        chart = VerticalBarChart()
        chart.x = 0.6*inch
        chart.y = 0.3*inch
        chart.width = 5.8*inch
        chart.height = 1.5*inch
        chart.data = [[float(v) for v in dataset]]
        chart.categoryAxis.categoryNames = label_names
        chart.bars[0].fillColor = color
        chart.valueAxis.valueMin = 0
        chart.valueAxis.labels.fontSize = 8
        chart.categoryAxis.labels.fontSize = 8
        chart.barLabelFormat = '%d'
        chart.barLabels.fontSize = 8
        chart.barLabels.nudge = 4
        drawing.add(chart)
        return drawing
        
    elements.append(Paragraph("Import Zip Archives", heading_style))
    elements.append(create_interval_chart("Import Zip Archives", labels, zip_values, cosigma_cyan))
    elements.append(Spacer(1, 0.3*inch))
    elements.append(Paragraph("Decompressed", heading_style))
    elements.append(create_interval_chart("Decompressed", labels, decomp_values, cosigma_blue))
    
    # 6. Page Break to Page 9 (Interval Charts Page 2)
    elements.append(PageBreak())
    elements.append(Paragraph("Credentials", heading_style))
    elements.append(create_interval_chart("Credentials", labels, cred_values, cosigma_cyan))
    elements.append(Spacer(1, 0.3*inch))
    elements.append(Paragraph("HWID", heading_style))
    elements.append(create_interval_chart("HWID", labels, hwid_values, cosigma_purple))
    
    # 7. Page Break to Page 10 (Alerts by Organization ID)
    elements.append(PageBreak())
    elements.append(Paragraph("Alerts by Organization ID", title_style))
    
    alerts_by_org = stats.get('alerts_by_org', {})
    sorted_alerts_by_org = sorted(alerts_by_org.items(), key=lambda x: x[1], reverse=True)
    
    orgs_table_data = [['Organization ID', 'Alerts Count']]
    for org_id, count in sorted_alerts_by_org:
        orgs_table_data.append([org_id, f"{count:,}"])
        
    if len(orgs_table_data) > 18:
        page10_data = orgs_table_data[:18]
        page11_data = [['Organization ID', 'Alerts Count']] + orgs_table_data[18:]
        
        table10 = Table(page10_data, colWidths=[4*inch, 2.5*inch])
        table10.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), cosigma_purple),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(table10)
        
        elements.append(PageBreak())
        elements.append(Paragraph("Alerts by Organization ID (continued)", title_style))
        table11 = Table(page11_data, colWidths=[4*inch, 2.5*inch])
        table11.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), cosigma_purple),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(table11)
    else:
        table10 = Table(orgs_table_data, colWidths=[4*inch, 2.5*inch])
        table10.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), cosigma_purple),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(table10)
        
    # 8. Page Break to Page 12 (Top Domain Occurrences)
    elements.append(PageBreak())
    elements.append(Paragraph("Top Domain Occurrences (from alerts)", title_style))
    
    top_domains_alerts = stats.get('top_domains_alerts', {})
    sorted_domains_alerts = sorted(top_domains_alerts.items(), key=lambda x: x[1], reverse=True)[:15]
    
    domains_table_data = [['Domain', 'Count']]
    for dom, count in sorted_domains_alerts:
        domains_table_data.append([dom[:45] + ('...' if len(dom) > 45 else ''), f"{count:,}"])
        
    doms_table = Table(domains_table_data, colWidths=[4.5*inch, 2*inch])
    doms_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), cosigma_orange),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(doms_table)
    
    # 9. Page Break to Page 13 (Pie Chart)
    elements.append(PageBreak())
    elements.append(Paragraph("Top 10 Domain Occurrences Distribution", title_style))
    elements.append(Spacer(1, 0.4*inch))
    
    top_10_domains = sorted_domains_alerts[:10]
    if len(top_10_domains) > 0:
        try:
            drawing = Drawing(7*inch, 4.8*inch)
            pie = Pie()
            pie.x = 1.2*inch
            pie.y = 0.6*inch
            pie.width = 3.8*inch
            pie.height = 3.8*inch
            pie.data = [float(count) for _, count in top_10_domains]
            pie.labels = [domain[:20] + ('...' if len(domain) > 20 else '') for domain, _ in top_10_domains]
            pie.slices.strokeWidth = 1.5
            pie.slices.strokeColor = colors.white
            
            colors_list = [cosigma_cyan, cosigma_blue, colors.HexColor('#10b981'), cosigma_orange,
                           cosigma_purple, colors.HexColor('#ec4899'), colors.HexColor('#f59e0b'),
                           colors.HexColor('#3b82f6'), colors.HexColor('#8b5cf6'), colors.HexColor('#14b8a6')]
                           
            for i in range(len(pie.slices)):
                if i < len(colors_list):
                    pie.slices[i].fillColor = colors_list[i]
            pie.sideLabels = 1
            pie.sideLabelsOffset = 0.2
            drawing.add(pie)
            elements.append(drawing)
        except Exception as chart_err:
            logger.error(f"Error rendering PDF pie chart: {chart_err}", exc_info=True)
            
    # Build Document
    doc.build(elements, onFirstPage=add_cover_page, onLaterPages=add_header_footer)
    buffer.seek(0)
    return buffer, end


@app.route('/api/export-pdf')
def export_pdf():
    """Export comprehensive statistics as professional PDF report."""
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        buffer, end = _generate_pdf_report(period, start_date, end_date)
        
        if period == 'daily':
            filename = f"Daily_report_{end.strftime('%Y-%m-%d')}.pdf"
        elif period == 'weekly':
            filename = f"Weekly_report_{end.strftime('%Y-%m-%d')}.pdf"
        else:
            filename = f"mongodb_report_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
            
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error exporting PDF: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/api/export-pdf-search', methods=['POST'])
def export_pdf_search():
    """Export search results as professional PDF report.
    
    Accepts JSON data in request body with:
    - type: 'domain_search', 'org_search', 'hwid_search', 'alerts_domain_search', 'report'
    - data: The search/report data to export
    - title: Optional title for the report
    """
    global PDF_STYLES
    
    # Initialize PDF styles
    styles = PDF_STYLES if PDF_STYLES is not None else None
    
    if styles is None:
        try:
            styles = getSampleStyleSheet()
            PDF_STYLES = styles
        except Exception as style_error:
            logger.error(f"Error initializing PDF styles: {style_error}", exc_info=True)
            return jsonify({'success': False, 'error': f'Failed to initialize PDF styles: {str(style_error)}'}), 500
    
    if styles is None:
        return jsonify({'success': False, 'error': 'PDF styles not available'}), 500
    
    try:
        logger.info("PDF export search endpoint called")
        # Get JSON data from request
        request_data = request.get_json()
        if not request_data:
            logger.error("No data provided in PDF export request")
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        search_type = request_data.get('type', 'report')
        data = request_data.get('data', {})
        title = request_data.get('title', 'Search Results Report')
        
        if search_type == 'report':
            # Use unified comprehensive PDF report layout for type == 'report'
            logger.info("Redirecting export_pdf_search report type to _generate_pdf_report")
            period = data.get('period', 'daily')
            start_date = data.get('start')
            end_date = data.get('end')
            
            buffer, end = _generate_pdf_report(period, start_date, end_date)
            
            if period == 'daily':
                filename = f"Daily_report_{end.strftime('%Y-%m-%d')}.pdf"
            elif period == 'weekly':
                filename = f"Weekly_report_{end.strftime('%Y-%m-%d')}.pdf"
            else:
                filename = f"mongodb_report_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
                
            return send_file(
                buffer,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename
            )
        
        logger.info(f"PDF export: type={search_type}, title={title}, data_keys={list(data.keys()) if isinstance(data, dict) else 'not_dict'}")
        
        # Cosigma Brand Colors
        cosigma_cyan = colors.HexColor('#06b6d4')
        cosigma_cyan_dark = colors.HexColor('#0891b2')
        cosigma_blue = colors.HexColor('#0ea5e9')
        cosigma_green = colors.HexColor('#10b981')
        cosigma_amber = colors.HexColor('#f59e0b')
        cosigma_purple = colors.HexColor('#a78bfa')
        cosigma_orange = colors.HexColor('#fb923c')
        cosigma_gray = colors.HexColor('#94a3b8')
        cosigma_dark = colors.HexColor('#1e293b')
        cosigma_light_gray = colors.HexColor('#f1f5f9')
        
        # Logo path
        logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
        logo_exists = os.path.exists(logo_path)
        
        buffer = BytesIO()
        
        # Header/Footer function
        def add_header_footer(canvas_obj, doc_obj):
            canvas_obj.saveState()
            page_width, page_height = A4
            
            # Header - Logo và COSIGMA
            logo_x = 0.5*inch
            logo_y = page_height - 0.5*inch - 0.6*inch
            logo_size = 0.7*inch
            
            if logo_exists:
                try:
                    canvas_obj.drawImage(logo_path, logo_x, logo_y, 
                                        width=logo_size, height=logo_size, preserveAspectRatio=True)
                except Exception:
                    pass
            
            text_x = logo_x + logo_size + 0.15*inch
            logo_center_y = logo_y + logo_size / 2
            text_y = logo_center_y - 0.15*inch
            
            canvas_obj.setFont('Times-Roman', 22)
            canvas_obj.setFillColor(colors.black)
            canvas_obj.drawString(text_x, text_y, 'BREACHUNT')
            
            # Footer
            footer_y = 0.4*inch
            canvas_obj.setFont('Helvetica', 8)
            canvas_obj.setFillColor(cosigma_gray)
            canvas_obj.drawCentredString(page_width / 2, footer_y + 0.15*inch, 'SIRET: 952 164 911 00010')
            canvas_obj.drawCentredString(page_width / 2, footer_y, '3 terrasse Valmy, 92800 PUTEAUX, France')
            
            canvas_obj.restoreState()
        
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=1.0*inch,
            bottomMargin=1.0*inch
        )
        elements = []
        
        # Styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=cosigma_cyan,
            spaceAfter=12,
            alignment=TA_LEFT,
            fontName='Helvetica-Bold',
            leading=28
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=cosigma_cyan,
            spaceAfter=14,
            spaceBefore=20,
            fontName='Helvetica-Bold',
            leading=20
        )
        
        metadata_style = ParagraphStyle(
            'Metadata',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#1e293b'),
            spaceAfter=6,
            alignment=TA_LEFT,
            fontName='Helvetica'
        )
        
        # Title
        elements.append(Paragraph(title, title_style))
        
        # Metadata
        metadata_table = Table([[
            Paragraph(f"<b>Report Type:</b> {search_type.replace('_', ' ').title()}", metadata_style),
            Paragraph(f"<b>Generated:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", metadata_style)
        ]], colWidths=[3.5*inch, 3.5*inch])
        metadata_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('LINEBELOW', (0, 0), (-1, 0), 2, cosigma_cyan),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 14),
            ('RIGHTPADDING', (0, 0), (-1, -1), 14),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0f172a')),
        ]))
        elements.append(metadata_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Process data based on type
        if search_type == 'domain_search' and data.get('results'):
            elements.append(Paragraph("Domain Search Results", heading_style))
            table_data = [['Organization Index', 'Display Name', 'Matching Domains', 'Total Domains']]
            for org in data['results'][:50]:  # Limit to 50 rows
                matching = ', '.join(org.get('matching_domains', [])[:3])
                if len(org.get('matching_domains', [])) > 3:
                    matching += '...'
                table_data.append([
                    str(org.get('organization_index', 'N/A'))[:30],
                    str(org.get('display_name', 'N/A'))[:40],
                    matching[:50],
                    str(org.get('total_domains', 0))
                ])
            
            table = Table(table_data, colWidths=[1.5*inch, 2*inch, 2.5*inch, 1*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), cosigma_cyan),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(KeepTogether(table))
            
        elif search_type == 'org_search' and data.get('results'):
            elements.append(Paragraph("Organization Search Results", heading_style))
            table_data = [['Organization Index', 'Display Name', 'Total Domains']]
            for org in data['results'][:50]:
                table_data.append([
                    str(org.get('organization_index', 'N/A'))[:30],
                    str(org.get('display_name', 'N/A'))[:50],
                    str(org.get('total_domains', 0))
                ])
            
            table = Table(table_data, colWidths=[2*inch, 4*inch, 1*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), cosigma_purple),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
                ('TOPPADDING', (0, 1), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 10),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
                ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(KeepTogether(table))
            
        elif search_type == 'hwid_search' and data.get('hwids'):
            elements.append(Paragraph("HWID Search Results", heading_style))
            table_data = [['HWID', 'Alert Count', 'Occurrence Count', 'First Seen', 'Last Seen']]
            for hwid in data['hwids'][:50]:
                table_data.append([
                    str(hwid.get('hwid', 'N/A'))[:40],
                    str(hwid.get('alert_count', 0)),
                    str(hwid.get('occurrence_count', 0)),
                    str(hwid.get('first_seen', 'N/A'))[:20],
                    str(hwid.get('last_seen', 'N/A'))[:20]
                ])
            
            table = Table(table_data, colWidths=[2*inch, 1*inch, 1.2*inch, 1.2*inch, 1.2*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), cosigma_orange),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(KeepTogether(table))
            
        elif search_type == 'alerts_domain_search' and data.get('results'):
            elements.append(Paragraph("Alerts Domain Search Results", heading_style))
            table_data = [['Alert ID', 'Matching Domains', 'Organization ID', 'Type', 'Created Date']]
            for alert in data['results'][:50]:
                matching = ', '.join(alert.get('matching_domains', [])[:2])
                if len(alert.get('matching_domains', [])) > 2:
                    matching += '...'
                table_data.append([
                    str(alert.get('alert_id', 'N/A'))[:30],
                    matching[:40],
                    str(alert.get('organization_id', 'N/A'))[:30],
                    str(alert.get('type', 'N/A'))[:20],
                    str(alert.get('created_date', 'N/A'))[:20]
                ])
            
            table = Table(table_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 1*inch, 1*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), cosigma_amber),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(KeepTogether(table))
            
        elif search_type == 'report' and data.get('stats'):
            # Report type - show statistics
            elements.append(Paragraph("Statistics Summary", heading_style))
            stats = data.get('stats', {})
            table_data = [
                ['Metric', 'Count'],
                ['Zip Archives Imported', f"{int(stats.get('zip_import', 0)):,}"],
                ['Decompressed Archives', f"{int(stats.get('decompressed', 0)):,}"],
                ['Credentials Found', f"{int(stats.get('credentials', 0)):,}"],
                ['HWID Found', f"{int(stats.get('hwid', 0)):,}"],
                ['Total Organizations', f"{int(stats.get('total_organizations', 0)):,}"],
                ['Total Domains', f"{int(stats.get('total_domains', 0)):,}"],
                ['Unique Domains', f"{int(stats.get('unique_domains', 0)):,}"]
            ]
            
            table = Table(table_data, colWidths=[4.5*inch, 2.5*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), cosigma_cyan),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 14),
                ('TOPPADDING', (0, 0), (-1, 0), 14),
                ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 10),
                ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
                ('TOPPADDING', (0, 1), (-1, -1), 11),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 11),
                ('LEFTPADDING', (0, 0), (-1, -1), 14),
                ('RIGHTPADDING', (0, 0), (-1, -1), 14),
            ]))
            elements.append(KeepTogether(table))
        
        # Build PDF
        logger.info(f"Building PDF with {len(elements)} elements")
        try:
            doc.build(elements, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
            buffer.seek(0)
            logger.info("PDF built successfully")
        except Exception as build_error:
            logger.error(f"Error building PDF: {build_error}", exc_info=True)
            return jsonify({'success': False, 'error': f'Failed to build PDF: {str(build_error)}'}), 500
        
        filename = f"{search_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
        
        logger.info(f"Sending PDF file: {filename}")
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error exporting search PDF: {e}", exc_info=True)
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Full traceback: {error_details}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _generate_report(period, hours=None, days=None):
    """Helper function to generate reports - reduces code duplication."""
    try:
        now = datetime.now(timezone.utc)
        if hours:
            start = now - timedelta(hours=hours)
        elif days:
            start = now - timedelta(days=days)
        else:
            raise ValueError("Either hours or days must be provided")
        
        end = now
        
        stats = get_stats_from_db(start, end)
        org_stats = get_organizations_stats()
        timestamps = get_data_timestamps()
        
        return jsonify({
            'success': True,
            'period': period,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'stats': {**stats, **org_stats},
            'dated': timestamps.get('dated', {})
        })
    except Exception as e:
        logger.error(f"Error generating {period} report: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/report/daily')
def daily_report():
    """Generate daily report (last 24 hours)."""
    logger.info("API /api/report/daily endpoint called")
    return _generate_report('daily', hours=24)


@app.route('/api/report/weekly')
def weekly_report():
    """Generate weekly report (last 7 days)."""
    logger.info("API /api/report/weekly endpoint called")
    return _generate_report('weekly', days=7)


@app.route('/api/search/domain')
def search_domain():
    """Search for domains in organizations. Returns organizations containing matching domains."""
    logger.info("API /api/search/domain endpoint called")
    try:
        domain_query = request.args.get('q', '').strip()
        try:
            limit = int(request.args.get('limit', 50))
            limit = max(1, min(limit, 100))  # Clamp between 1 and 100
        except (ValueError, TypeError):
            limit = 50
        
        if not domain_query:
            return jsonify({'success': False, 'error': 'Domain query parameter (q) is required'}), 400
        
        org_col = get_collection('organizations')
        
        # Search for organizations containing the domain (case-insensitive partial match)
        pipeline = [
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""},
                "domains": {"$regex": domain_query, "$options": "i"}  # Case-insensitive search
            }},
            {"$group": {
                "_id": "$_id",
                "display_name": {"$first": "$display_name"},
                "all_domains": {"$push": "$domains"},
                "matching_domains": {"$push": "$domains"},
                "created_at": {"$first": "$created_at"},
                "updated_at": {"$first": "$updated_at"}
            }},
            {"$limit": limit}
        ]
        
        results = list(org_col.aggregate(pipeline, allowDiskUse=True))
        
        # Format results - filter matching domains
        formatted_results = []
        for org in results:
            all_domains = org.get('all_domains', [])
            matching_domains = [d for d in all_domains if domain_query.lower() in d.lower()]
            # Handle dates - can be datetime object or string
            created_at = _format_date_safe(org.get('created_at'))
            updated_at = _format_date_safe(org.get('updated_at'))
            
            formatted_results.append({
                'organization_index': str(org['_id']),
                'display_name': org.get('display_name', 'N/A'),
                'matching_domains': matching_domains,
                'all_domains': all_domains,
                'total_domains': len(all_domains),
                'matching_count': len(matching_domains),
                'created_at': created_at,
                'updated_at': updated_at
            })
        
        return jsonify({
            'success': True,
            'query': domain_query,
            'total_organizations': len(formatted_results),
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error searching domain: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/search/organization')
def search_organization():
    """Search for organizations by index or display name."""
    logger.info("API /api/search/organization endpoint called")
    try:
        org_query = request.args.get('q', '').strip()
        try:
            limit = int(request.args.get('limit', 50))
            limit = max(1, min(limit, 100))  # Clamp between 1 and 100
        except (ValueError, TypeError):
            limit = 50
        
        if not org_query:
            return jsonify({'success': False, 'error': 'Organization query parameter (q) is required'}), 400
        
        org_col = get_collection('organizations')
        
        # Try to match by _id first, then by display_name
        query = {
            "$or": [
                {"_id": {"$regex": org_query, "$options": "i"}},
                {"display_name": {"$regex": org_query, "$options": "i"}}
            ]
        }
        
        results = list(org_col.find(query).limit(limit))
        
        # Format results
        formatted_results = []
        for org in results:
            domains = org.get('domains', [])
            
            # Handle dates - can be datetime object or string
            created_at = _format_date_safe(org.get('created_at'))
            updated_at = _format_date_safe(org.get('updated_at'))
            
            formatted_results.append({
                'organization_index': str(org['_id']),
                'display_name': org.get('display_name', 'N/A'),
                'domains': domains,
                'total_domains': len(domains) if isinstance(domains, list) else 0,
                'created_at': created_at,
                'updated_at': updated_at
            })
        
        return jsonify({
            'success': True,
            'query': org_query,
            'total_found': len(formatted_results),
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error searching organization: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/domain/indexes')
def get_domain_indexes():
    """Get all organization indexes that contain a specific domain."""
    logger.info("API /api/domain/indexes endpoint called")
    try:
        domain = request.args.get('domain', '').strip()
        
        if not domain:
            return jsonify({'success': False, 'error': 'Domain parameter is required'}), 400
        
        org_col = get_collection('organizations')
        
        # Find all organizations containing this domain
        pipeline = [
            {"$match": {"domains": domain}},
            {"$project": {
                "_id": 1,
                "display_name": 1,
                "domains": 1,
                "created_at": 1,
                "updated_at": 1
            }}
        ]
        
        results = list(org_col.aggregate(pipeline, allowDiskUse=True))
        
        # Format results
        formatted_results = []
        for org in results:
            # Handle dates - can be datetime object or string
            created_at = _format_date_safe(org.get('created_at'))
            updated_at = _format_date_safe(org.get('updated_at'))
            
            formatted_results.append({
                'organization_index': str(org['_id']),
                'display_name': org.get('display_name', 'N/A'),
                'domain': domain,
                'all_domains': org.get('domains', []),
                'created_at': created_at,
                'updated_at': updated_at
            })
        
        return jsonify({
            'success': True,
            'domain': domain,
            'total_organizations': len(formatted_results),
            'organization_indexes': [r['organization_index'] for r in formatted_results],
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error getting domain indexes: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alerts/domains/count')
def count_alerts_domains():
    """Count domain occurrences in alerts collection (backoffice/alert/domains).
    
    Since domains is an array, uses $unwind to count each domain occurrence.
    Supports filtering by domain pattern using $regex and time period based on updated_date.
    """
    logger.info("API /api/alerts/domains/count endpoint called")
    try:
        alerts_col = get_collection('alerts')
        
        # Get optional query parameters
        domain_pattern = request.args.get('domain', '').strip()
        period = request.args.get('period', 'weekly')  # daily or weekly
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        try:
            limit = int(request.args.get('limit', 100))
            limit = max(1, min(limit, 500))  # Clamp between 1 and 500
        except (ValueError, TypeError):
            limit = 100
        
        # Parse date range for updated_date filter (alerts uses updated_date)
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0 (parse_date_range already normalizes, but be explicit)
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        # Build aggregation pipeline
        pipeline = [
            # First filter by updated_date (time period filter) - alerts collection
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            # Unwind domains array to get individual domain values
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            # Filter out null/empty domains
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        # Add domain pattern filter if provided
        if domain_pattern:
            pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}  # Case-insensitive regex
                }
            })
        
        # Group by domain and count occurrences
        pipeline.extend([
            {"$group": {
                "_id": "$domains",
                "count": {"$sum": 1},
                # Also get sample document IDs for reference
                "sample_doc_ids": {"$push": "$_id"}
            }},
            {"$project": {
                "_id": 0,
                "domain": "$_id",
                "count": 1,
                "sample_doc_ids": {"$slice": ["$sample_doc_ids", 5]}  # First 5 doc IDs as samples
            }},
            {"$sort": {"count": -1}},  # Sort by count descending
            {"$limit": limit}
        ])
        
        results = list(alerts_col.aggregate(pipeline, allowDiskUse=True))
        
        # Calculate total unique domains and total occurrences
        total_pipeline = [
            # First filter by updated_date (time period filter) - alerts collection
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        # Add domain pattern filter if provided
        if domain_pattern:
            total_pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}
                }
            })
        
        total_pipeline.append({
            "$facet": {
                "total_occurrences": [
                    {"$count": "count"}
                ],
                "unique_domains": [
                    {"$group": {"_id": "$domains"}},
                    {"$count": "count"}
                ]
            }
        })
        
        total_result = list(alerts_col.aggregate(total_pipeline, allowDiskUse=True))
        
        total_occurrences = 0
        unique_domains = 0
        
        if total_result and total_result[0]:
            facets = total_result[0]
            total_occurrences = facets.get('total_occurrences', [{}])[0].get('count', 0) if facets.get('total_occurrences') else 0
            unique_domains = facets.get('unique_domains', [{}])[0].get('count', 0) if facets.get('unique_domains') else 0
        
        return jsonify({
            'success': True,
            'collection': 'alerts',
            'date_field': 'updated_date',
            'domain_pattern': domain_pattern if domain_pattern else None,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_occurrences': total_occurrences,
            'unique_domains': unique_domains,
            'total_returned': len(results),
            'results': results
        })
    except Exception as e:
        logger.error(f"Error counting alerts domains: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/archives/domains/count')
def count_archives_domains():
    """Count domain occurrences in archives collection (archives/archives).
    
    Note: Archives collection may not have domains field directly.
    This endpoint is prepared for future use if archives have domain-related data.
    Uses inserted_time for time period filtering.
    """
    logger.info("API /api/archives/domains/count endpoint called")
    try:
        archives_col = get_collection('archives')
        
        # Get optional query parameters
        domain_pattern = request.args.get('domain', '').strip()
        period = request.args.get('period', 'weekly')  # daily or weekly
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        try:
            limit = int(request.args.get('limit', 100))
            limit = max(1, min(limit, 500))
        except (ValueError, TypeError):
            limit = 100
        
        # Parse date range for inserted_time filter (archives uses inserted_time)
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0 (parse_date_range already normalizes, but be explicit)
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        # Check if archives collection has domains field
        sample_doc = archives_col.find_one({}, {"domains": 1})
        has_domains = sample_doc and 'domains' in sample_doc
        
        if not has_domains:
            return jsonify({
                'success': True,
                'collection': 'archives',
                'date_field': 'inserted_time',
                'message': 'Archives collection does not have domains field',
                'period': period,
                'start_date': start.isoformat(),
                'end_date': end.isoformat(),
                'total_occurrences': 0,
                'unique_domains': 0,
                'total_returned': 0,
                'results': []
            })
        
        # Build aggregation pipeline
        pipeline = [
            # First filter by inserted_time (time period filter) - archives collection
            {"$match": {
                "inserted_time": {"$gte": start, "$lte": end}
            }},
            # Unwind domains array if it exists
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        if domain_pattern:
            pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}
                }
            })
        
        pipeline.extend([
            {"$group": {
                "_id": "$domains",
                "count": {"$sum": 1},
                "sample_doc_ids": {"$push": "$_id"}
            }},
            {"$project": {
                "_id": 0,
                "domain": "$_id",
                "count": 1,
                "sample_doc_ids": {"$slice": ["$sample_doc_ids", 5]}
            }},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ])
        
        results = list(archives_col.aggregate(pipeline, allowDiskUse=True))
        
        # Calculate totals
        total_pipeline = [
            {"$match": {
                "inserted_time": {"$gte": start, "$lte": end}
            }},
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        if domain_pattern:
            total_pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}
                }
            })
        
        total_pipeline.append({
            "$facet": {
                "total_occurrences": [{"$count": "count"}],
                "unique_domains": [
                    {"$group": {"_id": "$domains"}},
                    {"$count": "count"}
                ]
            }
        })
        
        total_result = list(archives_col.aggregate(total_pipeline, allowDiskUse=True))
        
        total_occurrences = 0
        unique_domains = 0
        
        if total_result and total_result[0]:
            facets = total_result[0]
            total_occurrences = facets.get('total_occurrences', [{}])[0].get('count', 0) if facets.get('total_occurrences') else 0
            unique_domains = facets.get('unique_domains', [{}])[0].get('count', 0) if facets.get('unique_domains') else 0
        
        return jsonify({
            'success': True,
            'collection': 'archives',
            'date_field': 'inserted_time',
            'domain_pattern': domain_pattern if domain_pattern else None,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_occurrences': total_occurrences,
            'unique_domains': unique_domains,
            'total_returned': len(results),
            'results': results
        })
    except Exception as e:
        logger.error(f"Error counting archives domains: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alerts/domains/search')
def search_alerts_domain():
    """Search for a specific domain in alerts collection using regex pattern.
    
    Supports time period filtering based on updated_date.
    Example: /api/alerts/domains/search?domain=cosigma.io&period=daily
    """
    logger.info("API /api/alerts/domains/search endpoint called")
    try:
        domain_query = request.args.get('domain', '').strip()
        period = request.args.get('period', 'weekly')  # daily or weekly
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        try:
            limit = int(request.args.get('limit', 50))
            limit = max(1, min(limit, 200))  # Clamp between 1 and 200
        except (ValueError, TypeError):
            limit = 50
        
        if not domain_query:
            return jsonify({'success': False, 'error': 'Domain query parameter (domain) is required'}), 400
        
        alerts_col = get_collection('alerts')
        
        # Parse date range for updated_date filter
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0 (parse_date_range already normalizes, but be explicit)
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        # Search for alerts containing the domain (case-insensitive regex match)
        pipeline = [
            # First filter by updated_date (time period filter)
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            {"$match": {
                "domains": {"$regex": domain_query, "$options": "i"}  # Case-insensitive regex
            }},
            {"$project": {
                "_id": 1,
                "domains": 1,
                "created_date": 1,
                "updated_date": 1,
                "organization_id": 1,
                "type": 1,
                "full_url": 1,
                "normalized_url": 1
            }},
            {"$limit": limit}
        ]
        
        results = list(alerts_col.aggregate(pipeline, allowDiskUse=True))
        
        # Count total matching documents
        count_pipeline = [
            # First filter by updated_date (time period filter)
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            {"$match": {
                "domains": {"$regex": domain_query, "$options": "i"}
            }},
            {"$count": "total"}
        ]
        
        count_result = list(alerts_col.aggregate(count_pipeline, allowDiskUse=True))
        total_count = count_result[0]['total'] if count_result else 0
        
        # Format results
        formatted_results = []
        for alert in results:
            domains = alert.get('domains', [])
            matching_domains = [d for d in domains if domain_query.lower() in d.lower()]
            
            formatted_results.append({
                'alert_id': str(alert['_id']),
                'matching_domains': matching_domains,
                'all_domains': domains,
                'organization_id': alert.get('organization_id'),
                'type': alert.get('type'),
                'created_date': _format_date_safe(alert.get('created_date')),
                'updated_date': _format_date_safe(alert.get('updated_date')),
                'full_url': alert.get('full_url'),
                'normalized_url': alert.get('normalized_url')
            })
        
        return jsonify({
            'success': True,
            'query': domain_query,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_matching_alerts': total_count,
            'returned_count': len(formatted_results),
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error searching alerts domain: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hwid/list')
def list_hwids():
    """List unique HWIDs from detections array with optional filtering.
    
    Returns list of unique HWIDs found in the specified time period.
    Uses same logic as build_hwid_pipeline: unwinds detections array and counts unique IDs.
    """
    logger.info(f"API /api/hwid/list endpoint called")
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        hwid_pattern = request.args.get('hwid_pattern', '').strip()
        limit = int(request.args.get('limit', 100))
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        alerts_col = get_collection('alerts')
        
        # Build pipeline to get unique HWIDs (similar to build_hwid_pipeline but returns list)
        pipeline = [
            # Step 1: Match date range and ensure detections exists and is array
            {"$match": {
                "created_date": {"$gte": start, "$lte": end},
                "detections": {"$exists": True, "$ne": None, "$type": "array", "$not": {"$size": 0}}
            }},
            # Step 2: Unwind detections array
            {"$unwind": "$detections"},
            # Step 3: Extract id from various possible paths
            {"$project": {
                "hwid": {
                    "$ifNull": [
                        "$detections.host.id",
                        {"$ifNull": [
                            "$detections.source.host.id",
                            None
                        ]}
                    ]
                },
                "alert_id": "$_id",
                "created_date": 1
            }},
            # Step 4: Filter out documents without hwid
            {"$match": {
                "hwid": {"$exists": True, "$ne": None, "$ne": "", "$type": "string"}
            }}
        ]
        
        # Add HWID pattern filter if provided (before grouping)
        if hwid_pattern:
            pipeline.append({
                "$match": {
                    "hwid": {"$regex": hwid_pattern, "$options": "i"}
                }
            })
        
        # Group by hwid to get unique IDs and collect alert info
        pipeline.extend([
            {"$group": {
                "_id": "$hwid",
                "alert_ids": {"$addToSet": "$alert_id"},
                "first_seen": {"$min": "$created_date"},
                "last_seen": {"$max": "$created_date"},
                "occurrence_count": {"$sum": 1}
            }},
            # Sort alphabetically
            {"$sort": {"_id": 1}},
            # Limit results
            {"$limit": limit}
        ])
        
        results = list(alerts_col.aggregate(pipeline, allowDiskUse=True))
        
        # Format results
        hwid_list = []
        for item in results:
            hwid_list.append({
                'hwid': item['_id'],
                'alert_count': len(item.get('alert_ids', [])),
                'occurrence_count': item.get('occurrence_count', 0),
                'first_seen': _format_date_safe(item.get('first_seen')),
                'last_seen': _format_date_safe(item.get('last_seen'))
            })
        
        return jsonify({
            'success': True,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'hwid_pattern': hwid_pattern if hwid_pattern else None,
            'total_unique_hwids': len(hwid_list),
            'hwids': hwid_list
        })
    except Exception as e:
        logger.error(f"Error listing HWIDs: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/debug/archives')
def debug_archives():
    """Debug endpoint to test archives query directly - matches script count_zip_import_daily.py."""
    try:
        from db_helpers import get_collection
        from datetime import datetime, timedelta, timezone
        
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        archives_col = get_collection('archives')
        
        # Test 1: Direct count query EXACTLY like script count_zip_import_daily.py
        # Script: query = {"inserted_time": {"$gte": one_day_ago}}
        script_query = {"inserted_time": {"$gte": start}}
        direct_count_script = archives_col.count_documents(script_query)
        
        # Test 2: Using aggregation pipeline (like dashboard)
        from db_helpers import build_count_pipeline
        zip_pipeline = build_count_pipeline('inserted_time', start, end, None, use_gte_only=True)
        zip_result = list(archives_col.aggregate(zip_pipeline, allowDiskUse=True))
        aggregation_count = zip_result[0]['total'] if zip_result else 0
        
        # Test 3: Get ALL documents with $gte (no limit to see all)
        all_docs = list(archives_col.find({
            "inserted_time": {"$gte": start}
        }).sort("inserted_time", -1).limit(10))
        
        # Test 4: Get current time and calculate one_day_ago exactly like script
        now_script_style = datetime.now(timezone.utc)
        one_day_ago_script_style = now_script_style - timedelta(hours=24)
        script_style_count = archives_col.count_documents({
            "inserted_time": {"$gte": one_day_ago_script_style}
        })
        
        return jsonify({
            'success': True,
            'query_info': {
                'period': period,
                'start_from_parse': start.isoformat(),
                'end_from_parse': end.isoformat(),
                'now_script_style': now_script_style.isoformat(),
                'one_day_ago_script_style': one_day_ago_script_style.isoformat()
            },
            'counts': {
                'direct_count_script_query': direct_count_script,
                'aggregation_pipeline_count': aggregation_count,
                'script_style_count': script_style_count
            },
            'pipeline_used': zip_pipeline,
            'sample_documents': [
                {
                    '_id': str(doc.get('_id')),
                    'inserted_time': doc.get('inserted_time').isoformat() if isinstance(doc.get('inserted_time'), datetime) else str(doc.get('inserted_time')),
                    'inserted_time_type': str(type(doc.get('inserted_time'))),
                    'is_decompressed': doc.get('is_decompressed', False)
                }
                for doc in all_docs
            ]
        })
    except Exception as e:
        logger.error(f"Error in debug endpoint: {e}", exc_info=True)
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


if __name__ == '__main__':
    import warnings
    # Suppress threading warnings on Windows
    warnings.filterwarnings('ignore', category=RuntimeWarning, module='werkzeug')
    
    try:
        # On Windows, disable reloader to avoid threading issues
        import platform
        use_reloader = platform.system() != 'Windows'
        
        # Log all registered routes
        print("\n" + "="*60)
        print("Flask Application Started")
        print("="*60)
        print(f"Registered Routes ({len(list(app.url_map.iter_rules()))}):")
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            if not rule.rule.startswith('/static'):
                print(f"  {rule.rule:40} -> {rule.endpoint}")
        print("="*60)
        print(f"Server running on http://0.0.0.0:5000")
        print(f"Access dashboard at http://localhost:5000")
        print("="*60 + "\n")
        
        app.run(
            debug=True, 
            host='0.0.0.0', 
            port=5000, 
            use_reloader=use_reloader,
            use_debugger=True
        )
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        print(f"Error starting server: {e}")
        import traceback
        traceback.print_exc()
