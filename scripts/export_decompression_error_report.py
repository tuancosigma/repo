import os
import sys
import re
import json
import csv
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.units import inch

# Load environment variables
load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
if not MONGO_URI:
    print("Error: MONGODB_URL environment variable is not set.")
    print("Please create a .env file with MONGODB_URL configuration.")
    sys.exit(1)

# Database / Collection settings
DB_NAME = "archives"
COL_NAME = "archives"
TIMEOUT_MS = 60000  # 60s timeout for script

# Path to telegram channel titles JSON mapping
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHANNEL_MAP_FILE = os.path.join(SCRIPT_DIR, "telegram_channel_titles.json")

def load_channel_map():
    """Load Telegram Channel ID -> Title mapping from JSON file."""
    if not os.path.exists(CHANNEL_MAP_FILE):
        print(f"Warning: Channel mapping file not found at {CHANNEL_MAP_FILE}")
        return {}
    try:
        with open(CHANNEL_MAP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading channel mapping file: {e}")
        return {}

def extract_channel_id(name):
    """Extract Telegram channel ID from archive name (e.g. Chat.3148259533_msg.554)."""
    if not name:
        return None
    match = re.search(r'chat\.(\d+)', name, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def generate_pdf_report(output_path, days_range, report_data, summary_errors, summary_channels):
    """Generate a professional PDF report for decompression failures."""
    try:
        # Cosigma Brand Colors (matching app.py)
        cosigma_cyan = colors.HexColor('#06b6d4')
        cosigma_blue = colors.HexColor('#0ea5e9')
        cosigma_purple = colors.HexColor('#a78bfa')
        cosigma_orange = colors.HexColor('#fb923c')
        cosigma_dark = colors.HexColor('#1e293b')
        cosigma_light_gray = colors.HexColor('#f8fafc')
        
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch
        )
        
        styles = getSampleStyleSheet()
        
        # Custom Styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=cosigma_cyan,
            spaceAfter=15,
            alignment=TA_LEFT,
            fontName='Helvetica-Bold',
            leading=28
        )
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=cosigma_cyan,
            spaceAfter=10,
            spaceBefore=15,
            fontName='Helvetica-Bold',
            leading=18
        )
        body_style = ParagraphStyle(
            'Body',
            parent=styles['Normal'],
            fontSize=10,
            textColor=cosigma_dark,
            spaceAfter=8,
            fontName='Helvetica',
            leading=14
        )
        table_text_style = ParagraphStyle(
            'TableText',
            parent=styles['Normal'],
            fontSize=8,
            textColor=cosigma_dark,
            fontName='Helvetica',
            leading=10
        )
        table_header_style = ParagraphStyle(
            'TableHeader',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.white,
            fontName='Helvetica-Bold',
            leading=11
        )
        
        elements = []
        
        # Title
        elements.append(Paragraph("Breachunt - Decompression Failures Report", title_style))
        
        # Metadata Table
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        meta_data = [
            [Paragraph(f"<b>Report Period:</b> Last {days_range} days", body_style),
             Paragraph(f"<b>Generated:</b> {now_str}", body_style)],
            [Paragraph(f"<b>Total Failed Archives:</b> {len(report_data)}", body_style),
             Paragraph("<b>Database:</b> archives.archives", body_style)]
        ]
        meta_table = Table(meta_data, colWidths=[3.5*inch, 3.5*inch])
        meta_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f1f5f9')),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 0.2*inch))
        
        # 1. Error Breakdown Table
        elements.append(Paragraph("Error Breakdown", heading_style))
        err_table_data = [[Paragraph("Error Type", table_header_style), Paragraph("Count", table_header_style), Paragraph("Percentage", table_header_style)]]
        for err_type, count in summary_errors.items():
            percentage = (count / len(report_data)) * 100 if len(report_data) > 0 else 0
            err_table_data.append([
                Paragraph(err_type, table_text_style),
                Paragraph(str(count), table_text_style),
                Paragraph(f"{percentage:.1f}%", table_text_style)
            ])
        err_table = Table(err_table_data, colWidths=[3*inch, 2*inch, 2*inch])
        err_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), cosigma_cyan),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(err_table)
        elements.append(Spacer(1, 0.25*inch))
        
        # 2. Top Channels Table
        elements.append(Paragraph("Top Channels with Failures", heading_style))
        sorted_channels = sorted(summary_channels.items(), key=lambda x: x[1], reverse=True)[:10]
        chan_table_data = [[Paragraph("Telegram Channel Title", table_header_style), Paragraph("Count", table_header_style), Paragraph("Percentage", table_header_style)]]
        for channel, count in sorted_channels:
            percentage = (count / len(report_data)) * 100 if len(report_data) > 0 else 0
            # Safe ascii name for PDF rendering
            safe_chan = channel.encode('ascii', 'ignore').decode('ascii').strip()
            if not safe_chan:
                safe_chan = "Channel (Unicode Name)"
            chan_table_data.append([
                Paragraph(safe_chan, table_text_style),
                Paragraph(str(count), table_text_style),
                Paragraph(f"{percentage:.1f}%", table_text_style)
            ])
        chan_table = Table(chan_table_data, colWidths=[4*inch, 1.5*inch, 1.5*inch])
        chan_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), cosigma_purple),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(chan_table)
        
        # Page Break to detailed list
        elements.append(PageBreak())
        
        # 3. Detailed Failure List
        elements.append(Paragraph("Detailed Failure List", title_style))
        elements.append(Paragraph("Showing decompression failures details (limit 150):", body_style))
        
        detail_table_data = [[
            Paragraph("Archive Name", table_header_style),
            Paragraph("Inserted Time", table_header_style),
            Paragraph("Channel Name", table_header_style),
            Paragraph("Error Type", table_header_style)
        ]]
        
        # Print up to 150 rows in PDF detail
        for row in report_data[:150]:
            safe_name = row["Archive Name"].encode('ascii', 'ignore').decode('ascii').strip()
            safe_chan = row["Channel Name"].encode('ascii', 'ignore').decode('ascii').strip()
            if not safe_chan:
                safe_chan = "Channel (Unicode)"
                
            # Wrap in paragraphs for auto-wrap
            name_p = Paragraph(safe_name[:40] + ('...' if len(safe_name) > 40 else ''), table_text_style)
            time_p = Paragraph(row["Inserted Time"].replace(" UTC", ""), table_text_style)
            chan_p = Paragraph(safe_chan[:30] + ('...' if len(safe_chan) > 30 else ''), table_text_style)
            type_p = Paragraph(row["Error Type"], table_text_style)
            
            detail_table_data.append([name_p, time_p, chan_p, type_p])
            
        detail_table = Table(detail_table_data, colWidths=[2.5*inch, 1.3*inch, 1.8*inch, 1.4*inch])
        detail_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), cosigma_cyan),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, cosigma_light_gray]),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        elements.append(detail_table)
        
        doc.build(elements)
        return True
    except Exception as e:
        print(f"Warning: Failed to generate PDF report: {e}")
        return False

def main():
    # Parse arguments
    days_range = 7
    if len(sys.argv) > 1:
        try:
            days_range = int(sys.argv[1])
        except ValueError:
            print("Usage: python export_decompression_error_report.py [number_of_days]")
            sys.exit(1)

    print("\n" + "=" * 80)
    print(f"      BREACHUNT DECOMPRESSION FAILURE REPORT GENERATOR")
    print("=" * 80)
    print(f"Period: Last {days_range} days")
    print(f"Database: {DB_NAME}.{COL_NAME}")
    
    # 1. Load channel mapping
    print("Loading Telegram channel name mapping...")
    channel_map = load_channel_map()
    print(f"Loaded {len(channel_map)} channel mappings.")

    # 2. Connect to MongoDB
    print("\nConnecting to MongoDB...")
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client[DB_NAME]
        collection = db[COL_NAME]
        client.admin.command("ping")
        print("MongoDB connection successful.")
    except Exception as e:
        print(f"Error: Failed to connect to MongoDB: {e}")
        sys.exit(1)

    # 3. Calculate date range
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days_range)
    print(f"Querying archives inserted between {start_date.isoformat()} and {now.isoformat()} UTC...")

    # 4. Build query
    query = {
        "is_decompressed": False,
        "inserted_time": {"$gte": start_date},
        "$or": [
            {"error_msg": {"$regex": "no password", "$options": "i"}},
            {"error_msg": {"$regex": "unknown", "$options": "i"}}
        ]
    }

    # 5. Execute query
    print("Running query on MongoDB...")
    try:
        cursor = collection.find(
            query,
            {"name": 1, "inserted_time": 1, "error_msg": 1, "path": 1, "_id": 0}
        ).sort("inserted_time", -1)
        
        results = list(cursor)
        print(f"Found {len(results)} failed decompression archives matching criteria.")
    except Exception as e:
        print(f"Error executing query: {e}")
        sys.exit(1)

    if not results:
        print("\nNo failed archives found for this period. Exiting.")
        sys.exit(0)

    # 6. Process results
    print("\nProcessing and classifying reports...")
    report_data = []
    summary_channels = {}
    summary_errors = {"No password match": 0, "Unknown to decompress": 0}

    for doc in results:
        name = doc.get("name", "N/A")
        path = doc.get("path", "")
        inserted_time = doc.get("inserted_time")
        error_msg = doc.get("error_msg", "")

        # Format date
        if isinstance(inserted_time, datetime):
            date_str = inserted_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            date_str = str(inserted_time)

        # Extract filename from path, otherwise fallback to name
        filename = os.path.basename(path) if path else name
        # Clean the name: strip chat.<digits>_msg.<digits>_ and optional @ (e.g. kingulp (295).txt)
        clean_name = re.sub(r'^chat\.\d+_msg\.\d+_(?:@)?', '', filename, flags=re.IGNORECASE)

        # Extract channel info
        channel_id = extract_channel_id(name)
        channel_title = "Unknown Channel"
        if channel_id:
            channel_title = channel_map.get(channel_id, f"Channel ID: {channel_id}")

        # Classify error message
        err_lower = error_msg.lower()
        if "no password" in err_lower:
            err_type = "No password match"
        elif "unknown" in err_lower:
            err_type = "Unknown to decompress"
        else:
            # Skip any error types that do not match the specified ones
            continue

        # Update stats
        summary_errors[err_type] += 1
        summary_channels[channel_title] = summary_channels.get(channel_title, 0) + 1

        report_data.append({
            "Archive Name": clean_name,
            "Inserted Time": date_str,
            "Channel ID": channel_id or "N/A",
            "Channel Name": channel_title,
            "Error Type": err_type,
            "Original Error": error_msg
        })

    if not report_data:
        print("\nNo failed archives matching the required error types found. Exiting.")
        sys.exit(0)

    # Sort report_data by Channel Name (alphabetically/grouped) and Inserted Time (newest first)
    # Python's sort is stable, so we sort by Inserted Time descending first, then by Channel Name
    report_data.sort(key=lambda x: x["Inserted Time"], reverse=True)
    report_data.sort(key=lambda x: x["Channel Name"].lower())

    # 7. Export to PDF
    # Use local time for filename date range to match user's local date expectations
    now_local = datetime.now()
    start_local = now_local - timedelta(days=days_range - 1)
    start_date_str = start_local.strftime("%m-%d")
    end_date_str = now_local.strftime("%m-%d")
    pdf_filename = f"decompression_failures_{start_date_str}_{end_date_str}_report.pdf"
    pdf_output_path = os.path.join(os.path.dirname(SCRIPT_DIR), pdf_filename)
    
    print(f"Generating PDF report: {pdf_filename}...")
    pdf_success = generate_pdf_report(pdf_output_path, days_range, report_data, summary_errors, summary_channels)
    if pdf_success:
        print("PDF report completed successfully.")
    else:
        print("Warning: PDF report generation failed.")

    # 8. Print Summary Table
    print("\n" + "=" * 80)
    print("                           SUMMARY STATISTICS")
    print("=" * 80)
    print(f"Total Failed Archives: {len(report_data)}")
    print("-" * 80)
    print("Error Breakdown:")
    for k, v in summary_errors.items():
        percentage = (v / len(report_data)) * 100
        print(f"  - {k:25}: {v:5} ({percentage:.1f}%)")
    
    print("-" * 80)
    print("Top 10 Channels with Failures:")
    sorted_channels = sorted(summary_channels.items(), key=lambda x: x[1], reverse=True)[:10]
    for channel, count in sorted_channels:
        # Safe ASCII representation of the channel name for the console print (preventing CP1252 crash)
        safe_channel = channel.encode('ascii', 'ignore').decode('ascii').strip()
        if not safe_channel:
            safe_channel = "Channel (Unicode Name)"
        percentage = (count / len(report_data)) * 100
        print(f"  - {safe_channel[:45]:45}: {count:5} ({percentage:.1f}%)")
    
    print("=" * 80)
    if pdf_success:
        print(f"PDF Report File: {pdf_output_path}")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    main()
