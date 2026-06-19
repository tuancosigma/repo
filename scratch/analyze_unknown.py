import os
import sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL is not set")
    sys.exit(1)

def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, directConnection=True)
    db = client["infostealer"]
    credentials = db["credentials"]
    
    # Calculate one_day_ago
    now = datetime.now(timezone.utc)
    one_day_ago = (now - timedelta(hours=24)).isoformat()
    
    print(f"Analyzing credentials in the last 24 hours (harvest_date >= {one_day_ago})...")
    
    # Let's run a single aggregation using facet to get both total count and grouping,
    # so they are computed on the exact same snapshot of the database.
    pipeline = [
        {"$match": {"harvest_date": {"$gte": one_day_ago}}},
        {"$facet": {
            "total_count": [{"$count": "count"}],
            "by_source_id": [
                {"$group": {"_id": "$source_id", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}}
            ],
            "by_source_type_field": [
                {"$group": {"_id": "$source.type", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}}
            ]
        }}
    ]
    
    print("Running aggregation...")
    results = list(credentials.aggregate(pipeline, hint="harvest_date_1", allowDiskUse=True))
    if not results:
        print("No results returned")
        return
        
    facet_res = results[0]
    total_count = facet_res["total_count"][0]["count"] if facet_res["total_count"] else 0
    by_source_id = facet_res["by_source_id"]
    by_source_type_field = facet_res["by_source_type_field"]
    
    print(f"Total count from facet: {total_count}")
    print("\nGroup by source_id:")
    sum_source_id = 0
    for item in by_source_id:
        val = item["_id"]
        cnt = item["count"]
        sum_source_id += cnt
        # Check type of source_id
        val_type = type(val).__name__ if val is not None else "None"
        print(f"  - source_id: {val} (type: {val_type}) -> count: {cnt}")
    print(f"Sum of source_id group counts: {sum_source_id}")
    
    print("\nGroup by source.type:")
    sum_source_type = 0
    for item in by_source_type_field:
        val = item["_id"]
        cnt = item["count"]
        sum_source_type += cnt
        print(f"  - source.type: {val} -> count: {cnt}")
    print(f"Sum of source.type group counts: {sum_source_type}")
    
    # Look at some examples of source_id
    print("\nFetching examples where source_id exists:")
    has_source_id_doc = credentials.find_one({"harvest_date": {"$gte": one_day_ago}, "source_id": {"$exists": True}})
    print("  - Doc with source_id:", has_source_id_doc)
    
    print("\nFetching examples where source_id does not exist:")
    no_source_id_doc = credentials.find_one({"harvest_date": {"$gte": one_day_ago}, "source_id": {"$exists": False}})
    print("  - Doc without source_id:", no_source_id_doc)

if __name__ == "__main__":
    main()
