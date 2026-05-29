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
        one_day_ago = (now - timedelta(hours=24)).isoformat()

        # Try to read from Flask app persistent cache first for instant response
        import time
        import json
        
        count = None
        cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'persistent_cache.json')
        
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    for entry in cache_data.values():
                        data, timestamp = entry
                        if isinstance(data, dict) and data.get('success') and data.get('period') == 'daily':
                            age = time.time() - timestamp
                            if age < 3600:  # Cache is fresh (1 hour)
                                val = data.get('stats', {}).get('credentials')
                                if val is not None and val > 0:
                                    count = val
                                    print("Retrieving count from persistent cache (instant)...")
                                    break
            except Exception:
                pass

        if count is None:
            print("Querying MongoDB (forcing harvest_date_1 index hint)...")
            query = {
                "harvest_date": {"$gte": one_day_ago}
            }
            # Explicitly force index usage to avoid slow collection scans on sharded 11-billion doc collection
            count = collection.count_documents(query, hint="harvest_date_1")

        print("-" * 40)
        print(f"Credentials found (24h) : {count:,}")
        print("-" * 40)

    except Exception as e:
        print(f"Error : {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
