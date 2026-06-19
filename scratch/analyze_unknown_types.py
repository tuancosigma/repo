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
    
    print("Fetching all sources...")
    sources_col = db["sources"]
    sources_cursor = sources_col.find({}, {"_id": 1, "type": 1})
    sources_dict = {}
    for s in sources_cursor:
        s_id = s.get("_id")
        s_type = s.get("type", "unknown")
        if s_id is not None:
            sources_dict[s_id] = s_type
            sources_dict[str(s_id)] = s_type
            
    # Also find if there are sources with type 'unknown'
    unknown_type_sources = sources_col.count_documents({"type": "unknown"})
    missing_type_sources = sources_col.count_documents({"type": {"$exists": False}})
    print(f"Sources in collection: {len(sources_dict) // 2}")
    print(f"Sources with explicit type='unknown': {unknown_type_sources}")
    print(f"Sources with missing type: {missing_type_sources}")
    
    # Calculate one_day_ago
    now = datetime.now(timezone.utc)
    one_day_ago = (now - timedelta(hours=24)).isoformat()
    
    print("\nQuerying credentials in last 24 hours...")
    credentials = db["credentials"]
    pipeline = [
        {"$match": {"harvest_date": {"$gte": one_day_ago}}},
        {"$group": {"_id": "$source_id"}}
    ]
    results = list(credentials.aggregate(pipeline, hint="harvest_date_1", allowDiskUse=True))
    
    none_count = 0
    not_in_sources = []
    
    for item in results:
        source_id = item.get('_id')
        if source_id is None:
            none_count += 1
            continue
            
        type_name = sources_dict.get(source_id)
        if type_name is None:
            type_name = sources_dict.get(str(source_id))
            
        if type_name is None:
            not_in_sources.append(source_id)
            
    print(f"Total unique source_ids in credentials (last 24h): {len(results)}")
    print(f"Number of None source_ids: {none_count}")
    print(f"Number of source_ids not found in 'sources' collection: {len(not_in_sources)}")
    if not_in_sources:
        print("Sample of missing source_ids:", not_in_sources[:10])
        # Find if these missing source_ids exist in sources collection under string/ObjectId format
        sample_id = not_in_sources[0]
        db_doc = sources_col.find_one({"_id": sample_id})
        print(f"Checking missing source_id {sample_id} directly in sources: {db_doc}")

if __name__ == "__main__":
    main()
