import os
import sys
import re
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL is not set in environment or .env file.")
    sys.exit(1)

def parse_archive_name(name):
    """Extract chat_id and message_id from archive name if possible."""
    # Match pattern: chat.<chat_id>_msg.<message_id>
    match = re.search(r'chat\.(\d+)_msg\.(\d+)', name, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None

def main():
    # 1. Parse input archive list
    archives = []
    
    # Check if file path is provided as argument
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        print(f"Reading archives from: {sys.argv[1]}")
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            archives = [line.strip() for line in f if line.strip()]
    # Check if standard input is piped
    elif not sys.stdin.isatty():
        print("Reading archives from standard input (stdin)...")
        archives = [line.strip() for line in sys.stdin if line.strip()]
    # Fallback to default check list
    else:
        default_file = os.path.join(os.path.dirname(__file__), "archives_to_check.txt")
        if os.path.exists(default_file):
            print(f"Reading archives from default list: {default_file}")
            with open(default_file, 'r', encoding='utf-8') as f:
                archives = [line.strip() for line in f if line.strip()]
        else:
            print("Error: No archives provided. Please provide a file, pipe logs, or ensure archives_to_check.txt exists.")
            sys.exit(1)

    # Clean the input line to extract just the archive name if user pasted full docker logs
    # e.g., "Replaying archive chat.3371841043_msg.2492_... to archives_processed"
    cleaned_archives = []
    for arch in archives:
        # Match pattern like "Replaying archive <name> to archives_processed" or similar
        replay_match = re.search(r'(?:replaying\s+archive\s+)?(chat\.\d+_msg\.\d+_\S+)(?:\s+to\s+.*)?', arch, re.IGNORECASE)
        if replay_match:
            cleaned_archives.append(replay_match.group(1))
        else:
            cleaned_archives.append(arch)

    cleaned_archives = list(set(cleaned_archives)) # Remove duplicates
    print(f"Total unique archives to check: {len(cleaned_archives)}")

    # 2. Connect to MongoDB
    print(f"Connecting to MongoDB...")
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, directConnection=True)
        client.admin.command("ping")
        print("Connected successfully.")
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        sys.exit(1)

    # Print indexes for context
    try:
        sources_indexes = client["infostealer"]["sources"].list_indexes()
        print("\nIndexes on infostealer.sources:")
        for index in sources_indexes:
            print(f"  - {index.get('name')}: {index.get('key')}")
            
        archives_indexes = client["archives"]["archives"].list_indexes()
        print("Indexes on archives.archives:")
        for index in archives_indexes:
            print(f"  - {index.get('name')}: {index.get('key')}")
    except Exception as e:
        print(f"Could not fetch indexes: {e}")

    # 3. Perform Checks in bulk (Optimized to prevent COLLSCANs timeout)
    print("\nChecking archives in MongoDB in bulk (optimized)...")
    
    # Pre-parse and prepare bulk lookups
    candidate_names = set()
    chat_msg_pairs = []
    
    for arch in cleaned_archives:
        clean_name = arch.strip()
        name_no_zip = clean_name[:-4] if clean_name.lower().endswith('.zip') else clean_name
        name_with_zip = clean_name if clean_name.lower().endswith('.zip') else f"{clean_name}.zip"
        
        candidate_names.add(clean_name)
        candidate_names.add(name_no_zip)
        candidate_names.add(name_with_zip)
        
        chat_id, message_id = parse_archive_name(clean_name)
        if chat_id is not None and message_id is not None:
            chat_msg_pairs.append({"chat_id": chat_id, "message_id": message_id})
            
    # Query infostealer.sources in bulk (ONE collection scan instead of 267 scans!)
    print("  Querying infostealer.sources...")
    sources_db = client["infostealer"]
    sources_col = sources_db["sources"]
    
    sources_query = {
        "$or": [
            {"archive_name": {"$in": list(candidate_names)}}
        ]
    }
    if chat_msg_pairs:
        sources_query["$or"].append({"$or": chat_msg_pairs})
        
    try:
        found_sources_docs = list(sources_col.find(sources_query))
        print(f"  Found {len(found_sources_docs)} source documents matching candidates.")
    except Exception as e:
        print(f"  Error querying infostealer.sources: {e}")
        found_sources_docs = []
    
    # Map documents to archives for fast O(1) matching in memory
    sources_by_archive = {}
    sources_by_id_pair = {} # Key: (chat_id, message_id)
    
    for doc in found_sources_docs:
        arch_name = doc.get("archive_name")
        if arch_name:
            sources_by_archive.setdefault(arch_name.strip(), []).append(doc)
            
        c_id = doc.get("chat_id")
        m_id = doc.get("message_id")
        if c_id is not None and m_id is not None:
            sources_by_id_pair.setdefault((int(c_id), int(m_id)), []).append(doc)
            
    # Query archives.archives in bulk
    print("  Querying archives.archives...")
    archives_db = client["archives"]
    archives_col = archives_db["archives"]
    
    archives_query = {
        "name": {"$in": list(candidate_names)}
    }
    try:
        found_archives_docs = list(archives_col.find(archives_query))
        print(f"  Found {len(found_archives_docs)} archive records.")
    except Exception as e:
        print(f"  Error querying archives.archives: {e}")
        found_archives_docs = []
    
    archives_by_name = {}
    for doc in found_archives_docs:
        name = doc.get("name")
        if name:
            archives_by_name[name.strip()] = doc
            
    # Process results mapping
    results = []
    found_in_sources = 0
    found_in_archives = 0
    decompression_failures = 0
    
    for arch in cleaned_archives:
        clean_name = arch.strip()
        name_no_zip = clean_name[:-4] if clean_name.lower().endswith('.zip') else clean_name
        name_with_zip = clean_name if clean_name.lower().endswith('.zip') else f"{clean_name}.zip"
        
        chat_id, message_id = parse_archive_name(clean_name)
        
        # Match sources
        matched_sources = []
        for possible_name in [clean_name, name_no_zip, name_with_zip]:
            if possible_name in sources_by_archive:
                matched_sources.extend(sources_by_archive[possible_name])
                
        if not matched_sources and chat_id is not None and message_id is not None:
            if (chat_id, message_id) in sources_by_id_pair:
                matched_sources.extend(sources_by_id_pair[(chat_id, message_id)])
                
        # Match archive
        matched_archive = None
        for possible_name in [clean_name, name_no_zip, name_with_zip]:
            if possible_name in archives_by_name:
                matched_archive = archives_by_name[possible_name]
                break
                
        in_sources = len(matched_sources) > 0
        in_archives = matched_archive is not None
        
        if in_sources:
            found_in_sources += 1
        if in_archives:
            found_in_archives += 1
            if matched_archive.get("is_decompressed") is False:
                decompression_failures += 1
                
        sources_sample = None
        if in_sources:
            first_doc = matched_sources[0]
            sources_sample = {
                "log_name": first_doc.get("log_name"),
                "source_file": first_doc.get("source_file"),
                "bucket": first_doc.get("bucket"),
                "publication_date": first_doc.get("publication_date"),
                "type": first_doc.get("type"),
                "query_method": "bulk lookup match"
            }
            
        results.append({
            "archive_name": clean_name,
            "parsed_chat_id": chat_id,
            "parsed_message_id": message_id,
            "in_sources": in_sources,
            "sources_count": len(matched_sources),
            "sources_sample": sources_sample,
            "in_archives": in_archives,
            "archives_status": {
                "is_decompressed": matched_archive.get("is_decompressed"),
                "error_msg": matched_archive.get("error_msg"),
                "inserted_time": str(matched_archive.get("inserted_time")) if matched_archive.get("inserted_time") else None
            } if matched_archive else None
        })

    # 4. Generate Report
    print("\n" + "="*80)
    print("CHECK SUMMARY REPORT")
    print("="*80)
    print(f"Total checked unique archives: {len(cleaned_archives)}")
    print(f"Found in 'infostealer.sources' collection : {found_in_sources} / {len(cleaned_archives)}")
    print(f"Found in 'archives.archives' collection      : {found_in_archives} / {len(cleaned_archives)}")
    if found_in_archives > 0:
        print(f"  └─ Failed to decompress (is_decompressed=False): {decompression_failures}")
    print("="*80)

    # 5. List archives not found in sources
    not_found_sources = [r for r in results if not r["in_sources"]]

    if not_found_sources:
        print(f"\nArchives NOT FOUND in 'infostealer.sources' ({len(not_found_sources)}):")
        for r in not_found_sources[:30]:
            status_in_arch = "Found in archives.archives" if r["in_archives"] else "NOT in archives.archives"
            if r["in_archives"] and r["archives_status"]:
                is_dec = r["archives_status"]["is_decompressed"]
                err = r["archives_status"]["error_msg"]
                status_in_arch += f" (is_decompressed={is_dec}, error='{err}')"
            print(f"  - {r['archive_name']} | [{status_in_arch}]")
        if len(not_found_sources) > 30:
            print(f"  ... and {len(not_found_sources) - 30} more.")

    # 6. Save detailed results to JSON
    output_json = os.path.join(os.path.dirname(__file__), "check_archives_results.json")
    try:
        import json
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nDetailed report saved to: {output_json}")
    except Exception as e:
        print(f"Failed to save JSON report: {e}")

if __name__ == "__main__":
    main()
