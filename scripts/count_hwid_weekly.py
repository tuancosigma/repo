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
DB_NAME = "backoffice"
COL_NAME = "alerts"

def main():
    try:
        print(f"Connecting to MongoDB : {DB_NAME}.{COL_NAME}...")
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        db = client[DB_NAME]
        collection = db[COL_NAME]

        client.admin.command("ping")

        now = datetime.now(timezone.utc)
        one_week_ago = now - timedelta(days=7)

        # IMPORTANT: detections is an array, we need to:
        # 1. Unwind the detections array
        # 2. Extract id from various paths (host.id, source.host.id, etc.)
        # 3. Count unique IDs (if duplicate, don't count again)
        pipeline = [
            # Step 1: Match date range and ensure detections exists and is array
            {"$match": {
                "created_date": {"$gte": one_week_ago},
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

        result = list(collection.aggregate(pipeline, allowDiskUse=True))
        count = result[0]['total'] if result else 0

        # Get list of unique HWIDs
        hwid_list_pipeline = [
            {"$match": {
                "created_date": {"$gte": one_week_ago},
                "detections": {"$exists": True, "$ne": None, "$type": "array", "$not": {"$size": 0}}
            }},
            {"$unwind": "$detections"},
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
            {"$match": {
                "hwid": {"$exists": True, "$ne": None, "$ne": "", "$type": "string"}
            }},
            {"$group": {
                "_id": "$hwid"
            }},
            {"$sort": {"_id": 1}}  # Sort alphabetically
        ]
        
        hwid_list_result = list(collection.aggregate(hwid_list_pipeline, allowDiskUse=True))
        hwid_list = [item["_id"] for item in hwid_list_result]

        print("-" * 40)
        print(f"HWID found (7 days, unique IDs) : {count:,}")
        print("-" * 40)
        if hwid_list:
            print(f"\nList of unique HWIDs ({len(hwid_list)} total):")
            for i, hwid in enumerate(hwid_list, 1):
                print(f"  {i}. {hwid}")
        else:
            print("\nNo HWIDs found.")
        print("-" * 40)

    except Exception as e:
        print(f"Error : {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
