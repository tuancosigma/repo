"""Count organizations and domains in backoffice.organizations collection."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
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


def count_organizations_indexes():
    """Count number of indexes in organizations collection."""
    client = get_mongo_client()
    org_col = client["backoffice"]["organizations"]
    
    indexes = list(org_col.list_indexes())
    index_count = len(indexes)
    
    print(f"\n[Indexes] Organizations collection has {index_count} indexes:")
    for idx in indexes:
        print(f"  - {idx.get('name', 'unnamed')}: {idx.get('key', {})}")
    
    client.close()
    return index_count


def count_organizations_and_domains():
    """Count organizations and total/unique domains."""
    client = get_mongo_client()
    org_col = client["backoffice"]["organizations"]
    
    # Count total organizations
    total_orgs = org_col.count_documents({})
    
    # Aggregate to count domains
    pipeline = [
        {
            "$project": {
                "_id": 1,
                "display_name": 1,
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
    ]
    
    result = list(org_col.aggregate(pipeline))
    
    if result:
        stats = result[0]
        total_domains = stats.get("total_domains", 0)
        orgs_with_domains = stats.get("organizations_with_domains", 0)
    else:
        total_domains = 0
        orgs_with_domains = 0
    
    # Count unique domains across all organizations
    unique_domains_pipeline = [
        {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$domains"}},
        {"$count": "unique_domains"}
    ]
    
    unique_result = list(org_col.aggregate(unique_domains_pipeline))
    unique_domains_count = unique_result[0]["unique_domains"] if unique_result else 0
    
    print(f"\n[Organizations] Total: {total_orgs}")
    print(f"[Domains] Total across all organizations: {total_domains}")
    print(f"[Domains] Unique domains: {unique_domains_count}")
    print(f"[Organizations] With domains: {orgs_with_domains}")
    
    # Show details for each organization
    print(f"\n[Details] Organizations and their domain counts:")
    orgs = org_col.find({}, {"_id": 1, "display_name": 1, "domains": 1})
    
    # Count domain occurrences across all organizations
    all_domains_count = {}
    
    for org in orgs:
        org_id = org.get("_id", "N/A")
        display_name = org.get("display_name", "N/A")
        domains = org.get("domains", [])
        domain_count = len(domains) if isinstance(domains, list) else 0
        
        print(f"\n  Organization: {org_id} ({display_name})")
        print(f"    Index Organization: {org_id} (this is the _id field)")
        print(f"    Total domains: {domain_count}")
        
        # Count occurrences of each domain in this organization
        domain_occurrences = {}
        for domain in domains:
            if domain:
                domain_occurrences[domain] = domain_occurrences.get(domain, 0) + 1
                all_domains_count[domain] = all_domains_count.get(domain, 0) + 1
        
        # Show domain occurrences for this organization
        if domain_occurrences:
            print(f"    Domain occurrences:")
            for domain, count in sorted(domain_occurrences.items()):
                print(f"      - {domain}: {count}")
    
    # Show overall domain occurrences across all organizations
    print(f"\n[Overall] Domain occurrences across ALL organizations:")
    if all_domains_count:
        for domain, count in sorted(all_domains_count.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {domain}: {count}")
    else:
        print("  No domains found")
    
    return {
        "total_organizations": total_orgs,
        "total_domains": total_domains,
        "unique_domains": unique_domains_count,
        "organizations_with_domains": orgs_with_domains,
        "client": client  # Return client for later use
    }


if __name__ == "__main__":
    print("=" * 60)
    print("ORGANIZATIONS AND DOMAINS COUNT")
    print("=" * 60)
    
    # Count indexes
    index_count = count_organizations_indexes()
    
    # Count organizations and domains
    stats = count_organizations_and_domains()
    
    # Get time ingest and dated info
    print("\n" + "=" * 60)
    print("TIME INGEST & DATED INFO")
    print("=" * 60)
    
    # Get client from stats
    client = stats.get("client")
    org_col = client["backoffice"]["organizations"]
    
    # Time ingest (from cache)
    cache_db = client["cache"]
    try:
        cache_orgs = cache_db["organizations"]
        latest_cache = cache_orgs.find_one(
            {"cached_at": {"$exists": True}},
            sort=[("cached_at", -1)]
        )
        if latest_cache:
            cached_at = latest_cache.get("cached_at")
            print(f"\n[Time Ingest] Latest cache ingestion:")
            print(f"  Organizations: {cached_at}")
    except:
        print(f"\n[Time Ingest] No cache data found for organizations")
    
    # Dated info (from source)
    orgs_dated = org_col.aggregate([
        {"$group": {
            "_id": None,
            "oldest_created": {"$min": "$created_at"},
            "newest_created": {"$max": "$created_at"},
            "oldest_updated": {"$min": "$updated_at"},
            "newest_updated": {"$max": "$updated_at"}
        }}
    ])
    
    dated_result = list(orgs_dated)
    if dated_result:
        dated = dated_result[0]
        print(f"\n[Data Dated] Organizations date range:")
        if dated.get("oldest_created"):
            print(f"  Created: {dated['oldest_created']} - {dated['newest_created']}")
        if dated.get("oldest_updated"):
            print(f"  Updated: {dated['oldest_updated']} - {dated['newest_updated']}")
    
    # Close client
    client.close()
    
    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("=" * 60)
    print(f"Indexes in organizations collection: {index_count}")
    print(f"Total organizations: {stats['total_organizations']}")
    print(f"Total domains (all organizations): {stats['total_domains']}")
    print(f"Unique domains: {stats['unique_domains']}")
    print(f"Organizations with domains: {stats['organizations_with_domains']}")
    print("\nNOTE:")
    print("- Index Organization = _id field of organization document")
    print("- Example: Organization 'cosigma' has index organization = 'cosigma'")
    print("- Domain occurrences show how many times each domain appears")
    print("- Time Ingest = When data was cached (cached_at field)")
    print("- Data Dated = Date range of source data (created_at/updated_at)")
    print("=" * 60)
