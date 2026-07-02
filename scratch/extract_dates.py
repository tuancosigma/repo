import json
from collections import Counter

with open("scratch/check_archives_results.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Filter only archives found in sources
found = [x for x in data if x.get("in_sources")]

# Sort detailed list by publication_date chronologically
found_sorted = sorted(
    found, 
    key=lambda x: x["sources_sample"]["publication_date"] if x["sources_sample"]["publication_date"] else ""
)

print(f"Total found in sources: {len(found_sorted)}")
print("\n--- Grouped by Date (from publication_date) ---")
dates = []
for x in found_sorted:
    pub_date = x["sources_sample"]["publication_date"]
    if pub_date:
        # Extract YYYY-MM-DD
        date_str = pub_date.split("T")[0]
        dates.append(date_str)
    else:
        dates.append("Unknown")

counter = Counter(dates)
for date, count in sorted(counter.items()):
    print(f"  - {date}: {count} archives")

print("\n--- Detailed List (Sorted by Date) ---")
current_date_group = None

for x in found_sorted:
    name = x["archive_name"]
    pub_date = x["sources_sample"]["publication_date"]
    bucket = x["sources_sample"]["bucket"]
    
    date_str = pub_date.split("T")[0] if pub_date else "Unknown Date"
    
    # Print a header when date changes to group them visually
    if date_str != current_date_group:
        current_date_group = date_str
        print(f"\n📅 {current_date_group}:")
        
    print(f"  - {name}")
    print(f"    └─ Publication Date: {pub_date} | Bucket: {bucket}")
