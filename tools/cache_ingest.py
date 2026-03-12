"""Cronjob script to aggregate and cache statistics data."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL environment variable is not set")
    sys.exit(1)

MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "3000"))


def get_mongo_client():
    """Get MongoDB client connection."""
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)


def setup_cache_collection():
    """Setup cache collections with TTL indexes (10 days)."""
    client = get_mongo_client()
    cache_db = client["cache"]
    
    # Collections for raw data cache
    collections = ["archives", "credentials", "alerts", "stats"]
    
    for coll_name in collections:
        cache_coll = cache_db[coll_name]
        try:
            cache_coll.create_index(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="idx_expires_at"
            )
        except Exception as e:
            pass  # Index may already exist
    
    client.close()


def aggregate_stats(start, end, period):
    """Aggregate statistics from source collections."""
    client = get_mongo_client()
    
    archives_col = client["archives"]["archives"]
    credentials_col = client["infostealer"]["credentials"]
    alerts_col = client["backoffice"]["alerts"]
    
    start_str = start.isoformat()
    end_str = end.isoformat()
    
    # Use aggregation pipeline for better performance
    zip_import_pipeline = [
        {"$match": {"inserted_time": {"$gte": start, "$lte": end}}},
        {"$count": "total"}
    ]
    
    decompressed_pipeline = [
        {"$match": {
            "is_decompressed": True,
            "inserted_time": {"$gte": start, "$lte": end}
        }},
        {"$count": "total"}
    ]
    
    credentials_pipeline = [
        {"$match": {"harvest_date": {"$gte": start_str, "$lte": end_str}}},
        {"$count": "total"}
    ]
    
    hwid_pipeline = [
        {"$match": {
            "created_date": {"$gte": start, "$lte": end},
            "detections.host.id": {"$exists": True, "$ne": None}
        }},
        {"$count": "total"}
    ]
    
    zip_result = list(archives_col.aggregate(zip_import_pipeline, allowDiskUse=True))
    decompressed_result = list(archives_col.aggregate(decompressed_pipeline, allowDiskUse=True))
    credentials_result = list(credentials_col.aggregate(credentials_pipeline, allowDiskUse=True))
    hwid_result = list(alerts_col.aggregate(hwid_pipeline, allowDiskUse=True))
    
    stats = {
        'zip_import': zip_result[0]['total'] if zip_result else 0,
        'decompressed': decompressed_result[0]['total'] if decompressed_result else 0,
        'credentials': credentials_result[0]['total'] if credentials_result else 0,
        'hwid': hwid_result[0]['total'] if hwid_result else 0
    }
    
    client.close()
    return stats


def aggregate_chart_data(start, end, intervals, delta):
    """Aggregate chart data from source collections."""
    client = get_mongo_client()
    
    archives_col = client["archives"]["archives"]
    credentials_col = client["infostealer"]["credentials"]
    alerts_col = client["backoffice"]["alerts"]
    
    labels = []
    datasets = {
        'zip_import': [],
        'decompressed': [],
        'credentials': [],
        'hwid': []
    }
    
    for i in range(intervals):
        interval_start = start + delta * i
        interval_end = interval_start + delta
        
        label_format = '%H:00' if delta.total_seconds() < 86400 else '%m/%d'
        labels.append(interval_start.strftime(label_format))
        
        start_str = interval_start.isoformat()
        end_str = interval_end.isoformat()
        
        zip_pipeline = [
            {"$match": {"inserted_time": {"$gte": interval_start, "$lt": interval_end}}},
            {"$count": "total"}
        ]
        
        decompressed_pipeline = [
            {"$match": {
                "is_decompressed": True,
                "inserted_time": {"$gte": interval_start, "$lt": interval_end}
            }},
            {"$count": "total"}
        ]
        
        credentials_pipeline = [
            {"$match": {"harvest_date": {"$gte": start_str, "$lt": end_str}}},
            {"$count": "total"}
        ]
        
        hwid_pipeline = [
            {"$match": {
                "created_date": {"$gte": interval_start, "$lt": interval_end},
                "detections.host.id": {"$exists": True, "$ne": None}
            }},
            {"$count": "total"}
        ]
        
        zip_result = list(archives_col.aggregate(zip_pipeline, allowDiskUse=True))
        decompressed_result = list(archives_col.aggregate(decompressed_pipeline, allowDiskUse=True))
        credentials_result = list(credentials_col.aggregate(credentials_pipeline, allowDiskUse=True))
        hwid_result = list(alerts_col.aggregate(hwid_pipeline, allowDiskUse=True))
        
        datasets['zip_import'].append(zip_result[0]['total'] if zip_result else 0)
        datasets['decompressed'].append(decompressed_result[0]['total'] if decompressed_result else 0)
        datasets['credentials'].append(credentials_result[0]['total'] if credentials_result else 0)
        datasets['hwid'].append(hwid_result[0]['total'] if hwid_result else 0)
    
    client.close()
    return labels, datasets


def copy_raw_data_to_cache(start, end, expires_at):
    """Copy raw documents from source collections to cache collections."""
    client = get_mongo_client()
    
    # Source collections
    archives_col = client["archives"]["archives"]
    credentials_col = client["infostealer"]["credentials"]
    alerts_col = client["backoffice"]["alerts"]
    
    # Cache collections
    cache_db = client["cache"]
    cache_archives = cache_db["archives"]
    cache_credentials = cache_db["credentials"]
    cache_alerts = cache_db["alerts"]
    
    start_str = start.isoformat()
    end_str = end.isoformat()
    
    copied_count = {"archives": 0, "credentials": 0, "alerts": 0}
    
    # Copy archives (all documents in date range)
    print("\n[1/3] Copying archives to cache...")
    archives_query = {"inserted_time": {"$gte": start, "$lte": end}}
    archives_docs = archives_col.find(archives_query)
    
    archive_batch = []
    for doc in archives_docs:
        # Use upsert to avoid duplicates (based on _id)
        doc_copy = dict(doc)
        doc_copy["expires_at"] = expires_at
        doc_copy["cached_at"] = datetime.now(timezone.utc)
        
        # Upsert based on original _id
        cache_archives.replace_one(
            {"_id": doc_copy["_id"]},
            doc_copy,
            upsert=True
        )
        copied_count["archives"] += 1
        
        if copied_count["archives"] % 1000 == 0:
            print(f"  ... Processed {copied_count['archives']} archive documents")
    
    print(f"  [OK] Copied/updated {copied_count['archives']} archive documents")
    
    # Copy credentials
    print("\n[2/3] Copying credentials to cache...")
    credentials_query = {"harvest_date": {"$gte": start_str, "$lte": end_str}}
    credentials_docs = credentials_col.find(credentials_query)
    
    for doc in credentials_docs:
        doc_copy = dict(doc)
        doc_copy["expires_at"] = expires_at
        doc_copy["cached_at"] = datetime.now(timezone.utc)
        
        cache_credentials.replace_one(
            {"_id": doc_copy["_id"]},
            doc_copy,
            upsert=True
        )
        copied_count["credentials"] += 1
        
        if copied_count["credentials"] % 1000 == 0:
            print(f"  ... Processed {copied_count['credentials']} credential documents")
    
    print(f"  [OK] Copied/updated {copied_count['credentials']} credential documents")
    
    # Copy alerts (only those with HWID)
    print("\n[3/3] Copying alerts (HWID) to cache...")
    alerts_query = {
        "created_date": {"$gte": start, "$lte": end},
        "detections.host.id": {"$exists": True, "$ne": None}
    }
    alerts_docs = alerts_col.find(alerts_query)
    
    for doc in alerts_docs:
        doc_copy = dict(doc)
        doc_copy["expires_at"] = expires_at
        doc_copy["cached_at"] = datetime.now(timezone.utc)
        
        cache_alerts.replace_one(
            {"_id": doc_copy["_id"]},
            doc_copy,
            upsert=True
        )
        copied_count["alerts"] += 1
        
        if copied_count["alerts"] % 1000 == 0:
            print(f"  ... Processed {copied_count['alerts']} alert documents")
    
    print(f"  [OK] Copied/updated {copied_count['alerts']} alert documents")
    
    client.close()
    return copied_count


def cleanup_expired_cache():
    """Clean up documents that are older than 10 days from now."""
    client = get_mongo_client()
    cache_db = client["cache"]
    
    now = datetime.now(timezone.utc)
    expire_threshold = now - timedelta(days=10)
    
    print("\n[Cleanup] Removing documents older than 10 days from cache...")
    
    collections = {
        "archives": cache_db["archives"],
        "credentials": cache_db["credentials"],
        "alerts": cache_db["alerts"]
    }
    
    total_deleted = 0
    for coll_name, cache_coll in collections.items():
        # Delete documents where expires_at < now (should be handled by TTL, but we do explicit cleanup)
        # Also delete documents that are older than 10 days based on date fields
        if coll_name == "archives":
            deleted = cache_coll.delete_many({
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"inserted_time": {"$lt": expire_threshold}}
                ]
            }).deleted_count
        elif coll_name == "credentials":
            deleted = cache_coll.delete_many({
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"harvest_date": {"$lt": expire_threshold.isoformat()}}
                ]
            }).deleted_count
        else:  # alerts
            deleted = cache_coll.delete_many({
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"created_date": {"$lt": expire_threshold}}
                ]
            }).deleted_count
        
        total_deleted += deleted
        if deleted > 0:
            print(f"  [OK] Deleted {deleted} expired documents from cache.{coll_name}")
    
    if total_deleted == 0:
        print("  [OK] No expired documents to delete")
    
    client.close()
    return total_deleted


def ingest_cache():
    """Main function to ingest data into cache (raw documents + aggregated stats).
    
    Cache retention: 10 days from NOW (current time)
    - Documents older than 10 days will be automatically deleted by TTL index
    - Each time cache_ingest runs, it updates expires_at = now + 10 days
    - This ensures cache always contains data from last 10 days
    """
    print("=" * 60)
    print("Starting cache ingestion...")
    print("=" * 60)
    
    setup_cache_collection()
    
    # Cleanup expired documents first
    cleanup_expired_cache()
    
    client = get_mongo_client()
    cache_db = client["cache"]
    stats_cache = cache_db["stats"]
    
    now = datetime.now(timezone.utc)
    # expires_at = now + 10 days (documents will auto-delete when expires_at < current_time)
    expires_at = now + timedelta(days=10)
    
    print(f"\n[INFO] Cache expiration: {expires_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"[INFO] Documents will auto-delete after 10 days from now")
    
    # Date range: last 10 days (to cover full cache retention period)
    cache_start = now - timedelta(days=10)
    cache_end = now
    
    # PART 1: Copy raw documents to cache (last 10 days)
    print("\n" + "=" * 60)
    print("PART 1: Copying raw documents to cache (last 10 days)")
    print("=" * 60)
    
    copied = copy_raw_data_to_cache(cache_start, cache_end, expires_at)
    
    print(f"\n[OK] Total documents copied to cache:")
    print(f"  - Archives: {copied['archives']}")
    print(f"  - Credentials: {copied['credentials']}")
    print(f"  - Alerts (HWID): {copied['alerts']}")
    
    # PART 2: Ingest aggregated stats (for backward compatibility with dashboard)
    print("\n" + "=" * 60)
    print("PART 2: Ingesting aggregated stats (for dashboard)")
    print("=" * 60)
    
    # Ingest daily stats
    print("\n[1/2] Ingesting daily stats...")
    daily_start = now - timedelta(hours=24)
    daily_end = now
    
    daily_stats = aggregate_stats(daily_start, daily_end, 'daily')
    
    daily_key = f"stats_daily_{daily_start.strftime('%Y%m%d')}"
    stats_cache.replace_one(
        {"_id": daily_key},
        {
            "_id": daily_key,
            "period": "daily",
            "start_date": daily_start.isoformat(),
            "end_date": daily_end.isoformat(),
            "data": daily_stats,
            "created_at": now,
            "expires_at": expires_at
        },
        upsert=True
    )
    print(f"  [OK] Cached daily stats: {daily_stats}")
    
    # Ingest daily chart data
    intervals = 24
    delta = timedelta(hours=1)
    daily_labels, daily_datasets = aggregate_chart_data(daily_start, daily_end, intervals, delta)
    
    chart_key = f"chart_daily_{daily_start.strftime('%Y%m%d')}"
    stats_cache.replace_one(
        {"_id": chart_key},
        {
            "_id": chart_key,
            "period": "daily",
            "start_date": daily_start.isoformat(),
            "end_date": daily_end.isoformat(),
            "labels": daily_labels,
            "datasets": daily_datasets,
            "created_at": now,
            "expires_at": expires_at
        },
        upsert=True
    )
    print(f"  [OK] Cached daily chart data ({len(daily_labels)} intervals)")
    
    # Ingest weekly stats
    print("\n[2/2] Ingesting weekly stats...")
    weekly_start = now - timedelta(days=7)
    weekly_end = now
    
    weekly_stats = aggregate_stats(weekly_start, weekly_end, 'weekly')
    
    weekly_key = f"stats_weekly_{weekly_start.strftime('%Y%m%d')}"
    stats_cache.replace_one(
        {"_id": weekly_key},
        {
            "_id": weekly_key,
            "period": "weekly",
            "start_date": weekly_start.isoformat(),
            "end_date": weekly_end.isoformat(),
            "data": weekly_stats,
            "created_at": now,
            "expires_at": expires_at
        },
        upsert=True
    )
    print(f"  [OK] Cached weekly stats: {weekly_stats}")
    
    # Ingest weekly chart data
    intervals = 7
    delta = timedelta(days=1)
    weekly_labels, weekly_datasets = aggregate_chart_data(weekly_start, weekly_end, intervals, delta)
    
    chart_weekly_key = f"chart_weekly_{weekly_start.strftime('%Y%m%d')}"
    stats_cache.replace_one(
        {"_id": chart_weekly_key},
        {
            "_id": chart_weekly_key,
            "period": "weekly",
            "start_date": weekly_start.isoformat(),
            "end_date": weekly_end.isoformat(),
            "labels": weekly_labels,
            "datasets": weekly_datasets,
            "created_at": now,
            "expires_at": expires_at
        },
        upsert=True
    )
    print(f"  [OK] Cached weekly chart data ({len(weekly_labels)} intervals)")
    
    client.close()
    
    print("\n" + "=" * 60)
    print("[OK] Cache ingestion completed successfully!")
    print(f"[OK] Raw documents cached for last 10 days")
    print(f"[OK] expires_at set to: {expires_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"[OK] Documents will AUTO-DELETE when expires_at < current_time")
    print(f"[OK] TTL index ensures documents > 10 days old are automatically removed")
    print("=" * 60)


if __name__ == "__main__":
    try:
        ingest_cache()
    except Exception as e:
        print(f"\n[ERROR] Error during cache ingestion: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
