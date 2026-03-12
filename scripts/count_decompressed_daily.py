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
DB_NAME = "archives"
COL_NAME = "archives"

def main():
    try:
        print(f"Connecting to MongoDB : {DB_NAME}.{COL_NAME}...")
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        db = client[DB_NAME]
        collection = db[COL_NAME]

        client.admin.command("ping")

        now = datetime.now(timezone.utc)
        one_day_ago = now - timedelta(hours=24)

        query = {
            "is_decompressed": True,
            "inserted_time": {"$gte": one_day_ago}
        }

        count = collection.count_documents(query)

        print("-" * 40)
        print(f"Archives (is_decompressed: true, 24h) : {count:,}")
        print("-" * 40)

    except Exception as e:
        print(f"Error : {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
