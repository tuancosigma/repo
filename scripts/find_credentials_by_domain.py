import os
import sys
import json
import argparse
from bson import ObjectId
from pymongo import MongoClient
from dotenv import load_dotenv

# Class for JSON serialization of BSON types like ObjectId
class MongoJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return super().default(o)

def main():
    parser = argparse.ArgumentParser(description="Query credentials from MongoDB by domain.")
    parser.add_argument("--domain", type=str, default="sdis59.fr", help="The target domain to search for")
    parser.add_argument("--limit", type=int, default=100, help="Max number of records to return (default: 100)")
    parser.add_argument("--output", type=str, default=None, help="Path to save results in JSON format")
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()
    mongo_uri = os.getenv("MONGODB_URL")
    if not mongo_uri:
        print("Error: MONGODB_URL environment variable is not set.")
        print("Please check your .env file.")
        sys.exit(1)

    db_name = "backoffice"
    col_name = "alerts"
    timeout_ms = 999999999

    try:
        print(f"Connecting to MongoDB...")
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=timeout_ms)
        db = client[db_name]
        collection = db[col_name]
        
        # Verify connection
        client.admin.command("ping")
        print("Connection successful!")
        
        # Build query
        # Using a regex allows matching both exact domain and subdomains (e.g. mail.sdis59.fr)
        target_domain = args.domain
        escaped_domain = target_domain.replace(".", "\\.")
        query = {
            "domains": {
                "$regex": escaped_domain,
                "$options": "i"
            }
        }
        
        print(f"Searching for up to {args.limit} alerts with domain matching: '{target_domain}'...")
        cursor = collection.find(query).limit(args.limit)
        
        records = list(cursor)
        count = len(records)
        print(f"Found {count} records matching domain '{target_domain}'.")
        
        if count == 0:
            print("No records found.")
            return

        # Print the records
        for i, record in enumerate(records, 1):
            print("-" * 60)
            print(f"Record #{i}:")
            print(f"  ID: {record.get('_id')}")
            print(f"  Created Date: {record.get('created_date')}")
            print(f"  Updated Date: {record.get('updated_date')}")
            print(f"  Organization ID: {record.get('organization_id')}")
            print(f"  Full URL: {record.get('full_url')}")
            print(f"  Domains: {record.get('domains')}")
            
            # Print full document structure beautifully
            print("  Details:")
            formatted_record = json.dumps(record, cls=MongoJSONEncoder, indent=2)
            print(formatted_record)

        if args.output:
            # Resolve relative output paths if needed
            output_path = os.path.abspath(args.output)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(records, f, cls=MongoJSONEncoder, indent=4, ensure_ascii=False)
            print(f"\n[+] Results successfully exported to: {output_path}")

    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
