"""Setup cache collection with TTL index for auto-deletion after 10 days."""
import os
import sys
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL environment variable is not set")
    sys.exit(1)

MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "3000"))


def setup_cache():
    """Setup cache collections with TTL indexes for raw data storage."""
    try:
        print("Connecting to MongoDB...")
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGODB_TIMEOUT_MS)
        
        client.admin.command("ping")
        print("[OK] Connected to MongoDB\n")
        
        cache_db = client["cache"]
        
        # Collections for raw data cache
        collections = {
            "archives": "Luu documents tu archives.archives",
            "credentials": "Luu documents tu infostealer.credentials",
            "alerts": "Luu documents tu backoffice.alerts",
            "stats": "Luu aggregated stats cho dashboard (backward compatibility)"
        }
        
        print("Creating TTL indexes on cache collections...")
        print("-" * 60)
        
        for coll_name, description in collections.items():
            cache_coll = cache_db[coll_name]
            
            # Create TTL index on expires_at field
            # MongoDB will automatically delete documents when expires_at < current time
            try:
                cache_coll.create_index(
                    [("expires_at", ASCENDING)],
                    expireAfterSeconds=0,  # Delete when expires_at < now
                    name="idx_expires_at"
                )
                print(f"[OK] TTL index created on cache.{coll_name}")
                print(f"     {description}")
            except Exception as e:
                print(f"[INFO] Index may already exist on cache.{coll_name}: {e}")
        
        print("\n" + "-" * 60)
        print("Cache retention: 10 days from NOW (auto-delete via TTL index)")
        print("Logic:")
        print("  - expires_at = now + 10 days (set each time cache_ingest runs)")
        print("  - MongoDB TTL index auto-deletes when expires_at < current_time")
        print("  - Documents older than 10 days are automatically removed")
        print("\nCollections:")
        print("  - cache.archives: Raw archive documents")
        print("  - cache.credentials: Raw credential documents")
        print("  - cache.alerts: Raw alert documents")
        print("  - cache.stats: Aggregated stats (backward compatibility)")
        print("-" * 60)
        
        # Verify indexes
        print("\nVerifying indexes:")
        for coll_name in collections.keys():
            cache_coll = cache_db[coll_name]
            indexes = list(cache_coll.list_indexes())
            print(f"\n  cache.{coll_name}:")
            for idx in indexes:
                print(f"    - {idx['name']}: {idx.get('key', {})}")
        
        client.close()
        
        print("\n" + "=" * 60)
        print("[OK] Cache collections setup completed!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Run cache_ingest.py to populate cache with raw data:")
        print("   python cache_ingest.py")
        print("\n2. Setup cronjob to run cache_ingest.py every 5 minutes:")
        print("   */5 * * * * cd /path/to/script_axilen && python cache_ingest.py")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[ERROR] Error setting up cache: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    setup_cache()
