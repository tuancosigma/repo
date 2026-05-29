import os
import sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL environment variable is not set")
    print("Please create a .env file with MONGODB_URL configuration")
    sys.exit(1)

MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "3000"))
DB_NAME = "infostealer"
COL_NAME = "credentials"

def main():
    try:
        print(f"Connecting to MongoDB : {DB_NAME}.{COL_NAME}...")
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        db = client[DB_NAME]
        collection = db[COL_NAME]

        client.admin.command("ping")

        now = datetime.now(timezone.utc)
        one_week_ago = (now - timedelta(days=7)).isoformat()

        # Try to read from Flask app persistent cache first for instant response
        import time
        import json
        
        count = None
        breakdown = {}
        cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'persistent_cache.json')
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    for entry in cache_data.values():
                        data, timestamp = entry
                        if isinstance(data, dict) and data.get('success') and data.get('period') == 'weekly':
                            age = time.time() - timestamp
                            if age < 3600:  # Cache is fresh (1 hour)
                                val = data.get('stats', {}).get('credentials')
                                if val is not None and val > 0:
                                    count = val
                                    breakdown = data.get('stats', {}).get('credential_types', {})
                                    print("Retrieving count from persistent cache (instant)...")
                                    break
            except Exception:
                pass

        queried_from_db = False
        if count is None:
            queried_from_db = True
            print("Querying MongoDB (forcing harvest_date_1 index hint)...")
            pipeline = [
                {"$match": {
                    "harvest_date": {"$gte": one_week_ago}
                }},
                {"$group": {
                    "_id": "$source.type",
                    "count": {"$sum": 1}
                }}
            ]
            # Explicitly force index usage to avoid slow collection scans on sharded 11-billion doc collection
            results = list(collection.aggregate(pipeline, hint="harvest_date_1", allowDiskUse=True))
            
            breakdown = {}
            count = 0
            for item in results:
                type_name = item.get('_id') or 'unknown'
                type_count = item.get('count', 0)
                breakdown[type_name] = type_count
                count += type_count

            # Write back to persistent cache to instantly synchronize dashboard
            try:
                if os.path.exists(cache_file):
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    
                    updated = False
                    for key, entry_val in cache_data.items():
                        entry, timestamp = entry_val
                        if isinstance(entry, dict) and entry.get('success') and entry.get('period') == 'weekly':
                            entry['stats']['credentials'] = count
                            entry['stats']['credential_types'] = breakdown
                            cache_data[key] = [entry, time.time()]
                            updated = True
                            
                    if updated:
                        with open(cache_file, 'w', encoding='utf-8') as f:
                            json.dump(cache_data, f, ensure_ascii=False, indent=2)
                        print("Saved updated count and breakdown to persistent cache file.")
            except Exception as e:
                print(f"Warning: Could not update persistent cache file: {e}")

        print("-" * 40)
        print(f"Credentials found (7 days) : {count:,}")
        print("-" * 40)
        if breakdown:
            print("Breakdown by source.type:")
            for type_name, type_count in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
                print(f"  - {type_name}: {type_count:,}")
            print("-" * 40)

    except Exception as e:
        print(f"Error : {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
