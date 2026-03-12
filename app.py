"""Flask web application for MongoDB Statistics Dashboard.

TIMEZONE POLICY:
- All datetime operations use UTC timezone (datetime.now(timezone.utc))
- All MongoDB queries use UTC datetime objects (UTC 0, +00:00)
- All date comparisons and calculations are in UTC 0
- Frontend displays dates in UTC format (with "UTC" suffix)
- France is UTC+1 (UTC+2 in summer), but all internal operations use UTC 0
- When displaying dates to users, they are shown in UTC 0 to maintain consistency
- IMPORTANT: All datetime objects read from MongoDB are normalized to UTC 0 using normalize_to_utc()
  This ensures that even if MongoDB stores dates with +08:00 (or any other timezone),
  all comparisons and operations use UTC 0 (+00:00) for consistency.
"""
from flask import Flask, render_template, jsonify, request, send_file, Response
from datetime import datetime, timedelta, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus.flowables import HRFlowable
from reportlab.pdfgen import canvas
from io import BytesIO
import os
import logging

from config import COLLECTIONS
from db_helpers import get_mongo_client, get_collection, execute_stats_queries, build_count_pipeline

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize PDF styles at module level
try:
    PDF_STYLES = getSampleStyleSheet()
except Exception as e:
    logger.error(f"Failed to initialize PDF styles at module level: {e}", exc_info=True)
    PDF_STYLES = None


def normalize_to_utc(dt):
    """Normalize datetime to UTC 0 (+00:00), converting from any timezone.
    
    Args:
        dt: datetime object (can be timezone-aware or naive, any timezone)
    
    Returns:
        datetime object in UTC 0 (+00:00)
    
    Examples:
        - Input: datetime(2026, 3, 18, 12, 48, 38, tzinfo=timezone(timedelta(hours=8)))  # +08:00
        - Output: datetime(2026, 3, 18, 4, 48, 38, tzinfo=timezone.utc)  # UTC 0
        
        - Input: datetime(2026, 3, 18, 12, 48, 38)  # naive
        - Output: datetime(2026, 3, 18, 12, 48, 38, tzinfo=timezone.utc)  # UTC 0 (assumed UTC)
    """
    if dt is None:
        return None
    
    if isinstance(dt, str):
        # Parse ISO string first
        dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
    
    if dt.tzinfo is None:
        # Naive datetime - assume it's already UTC and add UTC timezone
        return dt.replace(tzinfo=timezone.utc)
    else:
        # Timezone-aware datetime - convert to UTC 0
        return dt.astimezone(timezone.utc)


def _format_date_safe(dt):
    """Safely format a date (datetime or string) to ISO string.
    
    Handles both datetime objects and strings, ensuring we never call isoformat() on a string.
    """
    if dt is None:
        return None
    
    if isinstance(dt, str):
        # Already a string, try to normalize if it has timezone info
        try:
            value_str = dt.replace('Z', '+00:00')
            dt_obj = datetime.fromisoformat(value_str)
            normalized_dt = normalize_to_utc(dt_obj)
            if isinstance(normalized_dt, datetime):
                return normalized_dt.isoformat()
            else:
                return str(normalized_dt) if normalized_dt else None
        except Exception as e:
            logger.warning(f"Error parsing date string '{dt}': {e}")
            return dt  # Keep as-is if parsing fails
    elif isinstance(dt, datetime):
        normalized_dt = normalize_to_utc(dt)
        if isinstance(normalized_dt, datetime):
            return normalized_dt.isoformat()
        else:
            return str(normalized_dt) if normalized_dt else None
    else:
        # Unknown type, convert to string
        return str(dt) if dt else None


def parse_date_range(period, start_date=None, end_date=None):
    """Parse and return start/end datetime based on period or custom dates.
    
    All datetime objects are normalized to UTC 0 (+00:00) for consistency.
    This ensures that even if MongoDB stores dates with +08:00, all comparisons
    and operations use UTC 0.
    
    Matches the logic from count_import_daily.py:
    - now = datetime.now(timezone.utc)
    - For daily: start = now - timedelta(hours=24), end = now
    - Query: {"inserted_time": {"$gte": start, "$lte": end}}
    
    No limit on date range.
    """
    now = datetime.now(timezone.utc)
    
    if start_date and end_date:
        # Handle different date formats from URL
        try:
            # Parse dates and normalize to UTC 0
            if 'Z' in start_date or '+' in start_date or start_date.endswith('UTC'):
                start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            else:
                # Parse without timezone and add UTC
                start = datetime.fromisoformat(start_date)
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
            
            if 'Z' in end_date or '+' in end_date or end_date.endswith('UTC'):
                end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            else:
                # Parse without timezone and add UTC
                end = datetime.fromisoformat(end_date)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
            
            # Normalize both to UTC 0 (convert from any timezone like +08:00 to +00:00)
            start = normalize_to_utc(start)
            end = normalize_to_utc(end)
        except (ValueError, AttributeError) as e:
            logger.warning(f"Error parsing date range, using default period: {e}")
            # Fallback to default period if parsing fails
            if period == 'daily':
                start = now - timedelta(hours=24)
            else:  # weekly (default)
                start = now - timedelta(days=7)
            end = now
    else:
        # Calculate based on period - matches count_import_daily.py logic
        # For daily: now - timedelta(hours=24) to now
        # For weekly: now - timedelta(days=7) to now
        if period == 'daily':
            start = now - timedelta(hours=24)  # Same as count_import_daily.py: one_day_ago = now - timedelta(hours=24)
        else:  # weekly (default)
            start = now - timedelta(days=7)
        end = now  # Always use current time as end
    
    return start, end


def get_data_timestamps():
    """Get date range information from source collections."""
    try:
        # Get oldest and newest dates from source collections
        dated_info = {}
        
        # Helper function to get date range for a collection
        def get_date_range(collection_name, date_field):
            try:
                coll = get_collection(collection_name)
                result = list(coll.aggregate([
                    {"$group": {
                        "_id": None,
                        "oldest": {"$min": f"${date_field}"},
                        "newest": {"$max": f"${date_field}"}
                    }}
                ], allowDiskUse=True))
                return result[0] if result else None
            except Exception as e:
                logger.warning(f"Error getting date range for {collection_name}: {e}")
                return None
        
        # Get date ranges for each collection
        for coll_name, coll_config in COLLECTIONS.items():
            try:
                if coll_name == 'organizations':
                    # Special handling for organizations
                    org_col = get_collection('organizations')
                    org_result = list(org_col.aggregate([
                        {"$group": {
                            "_id": None,
                            "oldest_created": {"$min": "$created_at"},
                            "newest_created": {"$max": "$created_at"},
                            "oldest_updated": {"$min": "$updated_at"},
                            "newest_updated": {"$max": "$updated_at"}
                        }}
                    ], allowDiskUse=True))
                    if org_result and org_result[0]:
                        org_data = org_result[0].copy()
                        # Convert datetime fields to ISO strings, normalized to UTC 0
                        for key in ['oldest_created', 'newest_created', 'oldest_updated', 'newest_updated']:
                            if key in org_data and org_data[key] is not None:
                                try:
                                    if isinstance(org_data[key], datetime):
                                        # Normalize to UTC 0 before converting to ISO string
                                        org_data[key] = normalize_to_utc(org_data[key]).isoformat()
                                    elif isinstance(org_data[key], str):
                                        # Parse and normalize if it's a string (e.g., from MongoDB with +08:00)
                                        # Handle both 'Z' and timezone offsets like '+08:00'
                                        value_str = org_data[key].replace('Z', '+00:00')
                                        value_dt = datetime.fromisoformat(value_str)
                                        # Normalize to UTC 0
                                        normalized_dt = normalize_to_utc(value_dt)
                                        # Ensure output is in UTC 0 format (+00:00)
                                        org_data[key] = normalized_dt.isoformat()
                                except Exception as e:
                                    logger.warning(f"Error normalizing {key} for organizations: {e}")
                                    # Keep as-is if parsing fails
                        org_data["field"] = "created_at/updated_at"
                        dated_info["organizations"] = org_data
                else:
                    date_field = coll_config.get('date_field')
                    if date_field:
                        date_range = get_date_range(coll_name, date_field)
                        if date_range:
                            oldest = date_range.get("oldest")
                            newest = date_range.get("newest")
                            # Normalize datetime to UTC 0 before converting to ISO string
                            if isinstance(oldest, datetime):
                                oldest = normalize_to_utc(oldest).isoformat()
                            elif isinstance(oldest, str):
                                # Parse and normalize if it's a string (e.g., from MongoDB with +08:00)
                                try:
                                    oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                                    oldest = normalize_to_utc(oldest_dt).isoformat()
                                except:
                                    pass  # Keep as-is if parsing fails
                            
                            if isinstance(newest, datetime):
                                newest = normalize_to_utc(newest).isoformat()
                            elif isinstance(newest, str):
                                # Parse and normalize if it's a string (e.g., from MongoDB with +08:00)
                                try:
                                    newest_dt = datetime.fromisoformat(newest.replace('Z', '+00:00'))
                                    newest = normalize_to_utc(newest_dt).isoformat()
                                except:
                                    pass  # Keep as-is if parsing fails
                            dated_info[coll_name] = {
                                "oldest": oldest,
                                "newest": newest,
                                "field": date_field
                            }
            except Exception as coll_error:
                logger.warning(f"Error processing collection {coll_name}: {coll_error}")
        
        return {"dated": dated_info}
    except Exception as e:
        logger.error(f"Error getting data timestamps: {e}", exc_info=True)
        # Always return dicts, never None
        return {"dated": {}}


def get_organizations_stats():
    """Get organizations and domains statistics - optimized with combined pipeline."""
    try:
        org_col = get_collection('organizations')
        
        # Count indexes (lightweight operation)
        try:
            indexes = list(org_col.list_indexes())
            index_count = len(indexes)
        except Exception as e:
            logger.warning(f"Error counting indexes: {e}")
            index_count = 0
        
        # Combined pipeline for organizations stats and unique domains
        combined_pipeline = [
            {
                "$project": {
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
                "$facet": {
                    "org_stats": [
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
                    ],
                    "unique_domains": [
                        {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
                        {"$match": {"domains": {"$ne": None, "$ne": ""}}},
                        {"$group": {"_id": "$domains"}},
                        {"$count": "unique_domains"}
                    ],
                    "domain_occurrences": [
                        {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
                        {"$match": {"domains": {"$ne": None, "$ne": ""}}},
                        {"$group": {"_id": "$domains", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": 20}  # Top 20 most frequent domains
                    ]
                }
            }
        ]
        
        result = list(org_col.aggregate(combined_pipeline, allowDiskUse=True))
        
        if result and result[0]:
            facets = result[0]
            
            # Extract organization stats
            org_stats = facets.get('org_stats', [{}])[0] if facets.get('org_stats') else {}
            total_orgs = org_stats.get("total_organizations", 0)
            total_domains = org_stats.get("total_domains", 0)
            orgs_with_domains = org_stats.get("organizations_with_domains", 0)
            
            # Extract unique domains count
            unique_result = facets.get('unique_domains', [{}])[0] if facets.get('unique_domains') else {}
            unique_domains = unique_result.get("unique_domains", 0)
            
            # Extract domain occurrences
            domain_occurrences_list = facets.get('domain_occurrences', [])
            domain_occurrences = {}
            for item in domain_occurrences_list:
                domain = item.get("_id")
                count = item.get("count", 0)
                if domain:  # Only include non-empty domains
                    domain_occurrences[domain] = count
            
            logger.info(f"Found {len(domain_occurrences)} unique domains in occurrences")
        else:
            total_orgs = 0
            total_domains = 0
            orgs_with_domains = 0
            unique_domains = 0
            domain_occurrences = {}
        
        return {
            "organizations_indexes": index_count,
            "total_organizations": total_orgs,
            "total_domains": total_domains,
            "unique_domains": unique_domains,
            "organizations_with_domains": orgs_with_domains,
            "domain_occurrences": domain_occurrences
        }
    except Exception as e:
        logger.error(f"Error getting organizations stats: {e}", exc_info=True)
        return {
            "organizations_indexes": 0,
            "total_organizations": 0,
            "total_domains": 0,
            "unique_domains": 0,
            "organizations_with_domains": 0,
            "domain_occurrences": {}
        }


def get_stats_from_db(start, end):
    """Get statistics from MongoDB source collections.
    
    Args:
        start: Start datetime for date-filtered stats (UTC 0)
        end: End datetime for date-filtered stats (UTC 0)
    
    Returns:
        dict: Combined statistics including date-filtered and organization stats
    """
    try:
        # Ensure start and end are UTC 0 datetime objects
        start = normalize_to_utc(start) if start else datetime.now(timezone.utc) - timedelta(hours=24)
        end = normalize_to_utc(end) if end else datetime.now(timezone.utc)
        
        # Log query parameters for debugging
        logger.info(f"Querying stats from {start.isoformat()} to {end.isoformat()} (UTC 0)")
        
        # Execute queries from source collections
        stats = execute_stats_queries(start, end)
        
        # Add organizations stats (not date-dependent)
        org_stats = get_organizations_stats()
        if org_stats:
            stats.update(org_stats)
        
        # Log results for debugging
        logger.info(f"Stats results: zip_import={stats.get('zip_import', 0)}, decompressed={stats.get('decompressed', 0)}")
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting stats from DB: {e}", exc_info=True)
        # Return empty stats on error to prevent frontend crashes
        return {
            'zip_import': 0,
            'decompressed': 0,
            'credentials': 0,
            'hwid': 0,
            'total_organizations': 0,
            'organizations_indexes': 0,
            'total_domains': 0,
            'unique_domains': 0,
            'organizations_with_domains': 0,
            'domain_occurrences': {}
        }


def get_chart_intervals(start, end, period):
    """Calculate chart intervals based on date range."""
    total_seconds = (end - start).total_seconds()
    
    if period == 'daily' or total_seconds <= 86400:
        intervals = min(24, max(1, int(total_seconds / 3600)))
        delta = timedelta(seconds=total_seconds / intervals)
    else:
        intervals = min(7, max(1, int(total_seconds / 86400)))
        delta = timedelta(seconds=total_seconds / intervals)
    
    return intervals, delta


def get_chart_data_optimized(start, end, intervals, delta):
    """Get chart data using optimized aggregation pipeline.
    
    Optimized to use facet for combining multiple queries and better error handling.
    
    Args:
        start: Start datetime
        end: End datetime
        intervals: Number of intervals
        delta: Time delta between intervals
    """
    try:
        archives_col = get_collection('archives')
        credentials_col = get_collection('credentials')
        alerts_col = get_collection('alerts')
        
        labels = []
        datasets = {
            'zip_import': [0] * intervals,
            'decompressed': [0] * intervals,
            'credentials': [0] * intervals,
            'hwid': [0] * intervals
        }
        
        # Generate labels
        label_format = '%H:00' if delta.total_seconds() < 86400 else '%m/%d'
        for i in range(intervals):
            interval_start = start + delta * i
            labels.append(interval_start.strftime(label_format))
        
        # Build pipelines using helper function for consistency
        # Process intervals sequentially but with better error handling
        for i in range(intervals):
            interval_start = start + delta * i
            interval_end = interval_start + delta
            
            try:
                # Use build_count_pipeline helper for consistency
                # ZIP archives: use only $gte (like script count_zip_import_daily.py)
                zip_pipeline = build_count_pipeline(
                    'inserted_time', interval_start, interval_end, None, use_gte_only=True
                )
                
                decompressed_pipeline = build_count_pipeline(
                    'inserted_time', interval_start, interval_end,
                    {'is_decompressed': True}
                )
                
                credentials_pipeline = build_count_pipeline(
                    'harvest_date', interval_start, interval_end, None
                )
                
                # HWID: use custom pipeline to count unique IDs from detections array
                from db_helpers import build_hwid_pipeline
                hwid_pipeline = build_hwid_pipeline(interval_start, interval_end)
                
                # Execute queries with error handling
                try:
                    zip_result = list(archives_col.aggregate(zip_pipeline, allowDiskUse=True))
                    datasets['zip_import'][i] = zip_result[0]['total'] if zip_result else 0
                except Exception as e:
                    logger.warning(f"Error querying zip_import for interval {i}: {e}")
                    datasets['zip_import'][i] = 0
                
                try:
                    decompressed_result = list(archives_col.aggregate(decompressed_pipeline, allowDiskUse=True))
                    datasets['decompressed'][i] = decompressed_result[0]['total'] if decompressed_result else 0
                except Exception as e:
                    logger.warning(f"Error querying decompressed for interval {i}: {e}")
                    datasets['decompressed'][i] = 0
                
                try:
                    credentials_result = list(credentials_col.aggregate(credentials_pipeline, allowDiskUse=True))
                    datasets['credentials'][i] = credentials_result[0]['total'] if credentials_result else 0
                except Exception as e:
                    logger.warning(f"Error querying credentials for interval {i}: {e}")
                    datasets['credentials'][i] = 0
                
                try:
                    hwid_result = list(alerts_col.aggregate(hwid_pipeline, allowDiskUse=True))
                    datasets['hwid'][i] = hwid_result[0]['total'] if hwid_result else 0
                except Exception as e:
                    logger.warning(f"Error querying hwid for interval {i}: {e}")
                    datasets['hwid'][i] = 0
                    
            except Exception as e:
                logger.warning(f"Error processing interval {i}: {e}")
                # Continue with next interval even if one fails
                continue
        
        return labels, datasets
    except Exception as e:
        logger.error(f"Error getting chart data: {e}", exc_info=True)
        # Return empty data on error with proper structure
        return labels if labels else [f"Interval {i+1}" for i in range(intervals)], {
            'zip_import': [0] * intervals,
            'decompressed': [0] * intervals,
            'credentials': [0] * intervals,
            'hwid': [0] * intervals
        }


@app.route('/')
def index():
    """Render dashboard page."""
    logger.info("Serving dashboard page")
    return render_template('dashboard.html')


@app.route('/api/stats')
def get_stats():
    """Get statistics API endpoint - always queries from source collections."""
    logger.info(f"API /api/stats endpoint called")
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Log query parameters
        logger.info(f"API /api/stats called - period={period}, start={start.isoformat()}, end={end.isoformat()}")
        
        # Query from source collections
        stats = get_stats_from_db(start, end)
        
        # Add dated info
        timestamps = get_data_timestamps()
        stats["dated"] = timestamps.get("dated", {})
        
        # Log results
        domain_occurrences_count = len(stats.get('domain_occurrences', {}))
        logger.info(f"API /api/stats response - zip_import={stats.get('zip_import', 0)}, domain_occurrences_count={domain_occurrences_count}")
        
        return jsonify({
            'success': True,
            'stats': stats,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/chart-data')
def get_chart_data():
    """Get chart data API endpoint - always queries from source collections."""
    logger.info(f"API /api/chart-data endpoint called")
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Query from source collections
        intervals, delta = get_chart_intervals(start, end, period)
        labels, datasets = get_chart_data_optimized(start, end, intervals, delta)
        
        return jsonify({
            'success': True,
            'labels': labels,
            'datasets': datasets
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export-csv')
def export_csv():
    """Export statistics as CSV with comprehensive data."""
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        stats = get_stats_from_db(start, end)
        
        # Ensure stats is a dict and has required keys
        if not isinstance(stats, dict):
            stats = {}
        
        # Get dated info
        timestamps = get_data_timestamps()
        
        # Build comprehensive CSV
        csv_lines = []
        
        # Header section
        csv_lines.append("COSIGMA - MongoDB Statistics Report")
        csv_lines.append(f"Report Period: {period.upper()}")
        csv_lines.append(f"Date Range: {start.strftime('%Y-%m-%d %H:%M:%S')} to {end.strftime('%Y-%m-%d %H:%M:%S')}")
        csv_lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        csv_lines.append("")
        
        # Main statistics
        csv_lines.append("MAIN STATISTICS")
        csv_lines.append("Metric,Count")
        csv_lines.append(f"Zip Archives Imported,{int(stats.get('zip_import', 0)):,}")
        csv_lines.append(f"Decompressed Archives,{int(stats.get('decompressed', 0)):,}")
        csv_lines.append(f"Credentials Found,{int(stats.get('credentials', 0)):,}")
        csv_lines.append(f"HWID Found,{int(stats.get('hwid', 0)):,}")
        csv_lines.append("")
        
        # Organizations statistics
        csv_lines.append("ORGANIZATIONS STATISTICS")
        csv_lines.append("Metric,Count")
        csv_lines.append(f"Total Organizations,{int(stats.get('total_organizations', 0)):,}")
        csv_lines.append(f"Organization Indexes,{int(stats.get('organizations_indexes', 0)):,}")
        csv_lines.append(f"Total Domains,{int(stats.get('total_domains', 0)):,}")
        csv_lines.append(f"Unique Domains,{int(stats.get('unique_domains', 0)):,}")
        csv_lines.append(f"Organizations with Domains,{int(stats.get('organizations_with_domains', 0)):,}")
        csv_lines.append("")
        
        # Domain occurrences (top 20)
        if stats.get('domain_occurrences'):
            csv_lines.append("TOP DOMAIN OCCURRENCES")
            csv_lines.append("Domain,Count")
            sorted_domains = sorted(
                stats['domain_occurrences'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:20]
            for domain, count in sorted_domains:
                csv_lines.append(f"{domain},{int(count):,}")
            csv_lines.append("")
        
        # Data date ranges
        if timestamps and timestamps.get('dated'):
            csv_lines.append("DATA DATE RANGES")
            csv_lines.append("Collection,Field,Oldest Date,Newest Date")
            for coll, info in timestamps['dated'].items():
                if not isinstance(info, dict):
                    continue
                field = info.get('field', 'N/A')
                oldest = info.get('oldest') or info.get('oldest_created') or 'N/A'
                newest = info.get('newest') or info.get('newest_created') or 'N/A'
                try:
                    oldest_str = 'N/A'
                    newest_str = 'N/A'
                    
                    if oldest != 'N/A' and oldest:
                        if isinstance(oldest, str):
                            oldest_dt = datetime.fromisoformat(oldest.replace('Z', '+00:00'))
                        elif isinstance(oldest, datetime):
                            oldest_dt = oldest
                        else:
                            oldest_dt = None
                        
                        if oldest_dt:
                            # Normalize to UTC 0 before formatting
                            oldest_dt = normalize_to_utc(oldest_dt)
                            oldest_str = oldest_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    
                    if newest != 'N/A' and newest:
                        if isinstance(newest, str):
                            newest_dt = datetime.fromisoformat(newest.replace('Z', '+00:00'))
                        elif isinstance(newest, datetime):
                            newest_dt = newest
                        else:
                            newest_dt = None
                        
                        if newest_dt:
                            # Normalize to UTC 0 before formatting
                            newest_dt = normalize_to_utc(newest_dt)
                            newest_str = newest_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    
                    csv_lines.append(f"{coll},{field},{oldest_str},{newest_str}")
                except (ValueError, AttributeError, TypeError) as e:
                    logger.warning(f"Error formatting date for {coll}: {e}")
                    csv_lines.append(f"{coll},{field},N/A,N/A")
        
        filename = f"mongodb_report_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        
        return Response(
            '\n'.join(csv_lines),
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'text/csv; charset=utf-8'
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export-pdf')
def export_pdf():
    """Export comprehensive statistics as professional PDF report."""
    global PDF_STYLES
    
    # Initialize PDF styles
    styles = PDF_STYLES if PDF_STYLES is not None else None
    
    if styles is None:
        try:
            styles = getSampleStyleSheet()
            PDF_STYLES = styles
        except Exception as style_error:
            logger.error(f"Error initializing PDF styles: {style_error}", exc_info=True)
            return jsonify({'success': False, 'error': f'Failed to initialize PDF styles: {str(style_error)}'}), 500
    
    if styles is None:
        return jsonify({'success': False, 'error': 'PDF styles not available'}), 500
    
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        stats = get_stats_from_db(start, end)
        
        # Ensure stats is a dict and has required keys
        if not isinstance(stats, dict):
            stats = {}
        
        # Get dated info
        timestamps = get_data_timestamps()
        
        # Cosigma Brand Colors - Modern Dark Theme
        cosigma_cyan = colors.HexColor('#06b6d4')      # Primary - Cyan
        cosigma_cyan_dark = colors.HexColor('#0891b2') # Darker Cyan
        cosigma_blue = colors.HexColor('#0ea5e9')      # Secondary - Blue
        cosigma_green = colors.HexColor('#10b981')     # Accent - Green
        cosigma_amber = colors.HexColor('#f59e0b')     # Warm accent
        cosigma_purple = colors.HexColor('#a78bfa')    # Purple
        cosigma_orange = colors.HexColor('#fb923c')    # Orange
        cosigma_gray = colors.HexColor('#94a3b8')      # Muted gray
        cosigma_dark = colors.HexColor('#1e293b')      # Dark gray
        cosigma_light_gray = colors.HexColor('#f1f5f9')  # Light gray
        cosigma_bg = colors.HexColor('#0f172a')        # Dark background
        
        # Logo path và report reference
        logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.png')
        logo_exists = os.path.exists(logo_path)
        report_ref = f"MDB-{period.upper()}-{datetime.now(timezone.utc).strftime('%y%m%d')}"
        
        # Tính toán số trang (ước lượng)
        estimated_pages = 1
        if stats.get('domain_occurrences'):
            estimated_pages = 2
        
        buffer = BytesIO()
        
        # Biến để lưu tổng số trang (sẽ được cập nhật sau)
        total_pages_var = [1]  # Sử dụng list để có thể thay đổi trong closure
        
        # Tạo hàm callback cho header và footer
        def add_header_footer(canvas_obj, doc_obj):
            """Add header and footer to every page."""
            canvas_obj.saveState()
            
            page_width, page_height = A4
            
            # Header - Logo Cosigma đẹp hơn ở trái
            logo_x = 0.5*inch
            logo_y = page_height - 0.5*inch - 0.6*inch
            logo_size = 0.7*inch
            
            if logo_exists:
                try:
                    # Vẽ logo với kích thước lớn hơn và đẹp hơn
                    canvas_obj.drawImage(logo_path, logo_x, logo_y, 
                                        width=logo_size, height=logo_size, preserveAspectRatio=True)
                except Exception:
                    pass
            
            # Text "COSIGMA" với serif font đẹp hơn bên cạnh logo - căn chỉnh với center của logo
            text_x = logo_x + logo_size + 0.15*inch
            # Căn chỉnh text_y với center vertical của logo
            logo_center_y = logo_y + logo_size / 2
            text_y = logo_center_y - 0.15*inch  # Điều chỉnh để text thẳng hàng với center logo
            
            # Sử dụng Times-Roman (serif) để đẹp hơn như trong template
            canvas_obj.setFont('Times-Roman', 22)
            canvas_obj.setFillColor(colors.black)
            canvas_obj.drawString(text_x, text_y, 'COSIGMA')
            
            # Footer - Thông tin công ty ở giữa (không có duplicate COSIGMA)
            footer_y = 0.4*inch
            
            # Footer - Chỉ hiển thị thông tin công ty, không có tên COSIGMA (đã có ở header)
            canvas_obj.setFont('Helvetica', 8)
            canvas_obj.setFillColor(cosigma_gray)
            canvas_obj.drawCentredString(page_width / 2, footer_y + 0.15*inch, 'SIRET: 952 164 911 00010')
            canvas_obj.drawCentredString(page_width / 2, footer_y, '3 terrasse Valmy, 92800 PUTEAUX, France')
            
            canvas_obj.restoreState()
        
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=1.0*inch,  # Tăng top margin cho header
            bottomMargin=1.0*inch  # Tăng bottom margin cho footer
        )
        elements = []
        
        # Title Style với Cosigma branding (header đã được vẽ trên mỗi trang)
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=28,
            textColor=cosigma_cyan,
            spaceAfter=12,
            alignment=TA_LEFT,
            fontName='Helvetica-Bold',
            leading=32,
            letterSpacing=0.5
        )
        
        # Subtitle Style
        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontSize=13,
            textColor=cosigma_blue,
            spaceAfter=20,
            alignment=TA_LEFT,
            fontName='Helvetica',
            leading=16
        )
        
        # Section Heading Style
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=cosigma_cyan,
            spaceAfter=14,
            spaceBefore=20,
            fontName='Helvetica-Bold',
            leading=20,
            borderWidth=0,
            borderPadding=0,
            leftIndent=0
        )
        
        # Metadata Style - Đảm bảo text đủ tương phản với background
        metadata_style = ParagraphStyle(
            'Metadata',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#1e293b'),  # Màu đen đậm để đọc rõ trên nền xanh nhạt
            spaceAfter=6,
            alignment=TA_LEFT,
            fontName='Helvetica'
        )
        
        # Report Title và Metadata
        period_text = "Daily Report (24 hours)" if period == 'daily' else "Weekly Report (7 days)"
        date_range = f"{start.strftime('%Y-%m-%d %H:%M')} to {end.strftime('%Y-%m-%d %H:%M')}"
        
        elements.append(Paragraph("MongoDB Statistics Report", title_style))
        elements.append(Paragraph(period_text, subtitle_style))
        
        # Metadata box với background gradient-like effect
        metadata_table = Table([
            [
                Paragraph(f"<b>Report Period:</b> {date_range}", metadata_style),
                Paragraph(f"<b>Generated:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", metadata_style)
            ]
        ], colWidths=[3.5*inch, 3.5*inch])
        metadata_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),  # Nền sáng hơn để text đọc rõ
            ('LINEBELOW', (0, 0), (-1, 0), 2, cosigma_cyan),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 14),
            ('RIGHTPADDING', (0, 0), (-1, -1), 14),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#0f172a')),  # Màu text đậm để đọc rõ
        ]))
        elements.append(metadata_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Main Statistics Table với styling đẹp hơn
        elements.append(Paragraph("Main Statistics", heading_style))
        table_data = [
            ['Metric', 'Count'],
            ['Zip Archives Imported', f"{int(stats.get('zip_import', 0)):,}"],
            ['Decompressed Archives', f"{int(stats.get('decompressed', 0)):,}"],
            ['Credentials Found', f"{int(stats.get('credentials', 0)):,}"],
            ['HWID Found', f"{int(stats.get('hwid', 0)):,}"]
        ]
        
        table = Table(table_data, colWidths=[4.5*inch, 2.5*inch])
        table.setStyle(TableStyle([
            # Header row - Modern cyan gradient effect
            ('BACKGROUND', (0, 0), (-1, 0), cosigma_cyan),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 14),
            ('TOPPADDING', (0, 0), (-1, 0), 14),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            # Data rows - Alternating light backgrounds
            ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
            ('TOPPADDING', (0, 1), (-1, -1), 11),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 11),
            ('LEFTPADDING', (0, 0), (-1, -1), 14),
            ('RIGHTPADDING', (0, 0), (-1, -1), 14),
        ]))
        elements.append(KeepTogether(table))
        elements.append(Spacer(1, 0.3*inch))
        
        # Organizations Statistics với purple header
        elements.append(Paragraph("Organizations Statistics", heading_style))
        org_table_data = [
            ['Metric', 'Count'],
            ['Total Organizations', f"{int(stats.get('total_organizations', 0)):,}"],
            ['Organization Indexes', f"{int(stats.get('organizations_indexes', 0)):,}"],
            ['Total Domains', f"{int(stats.get('total_domains', 0)):,}"],
            ['Unique Domains', f"{int(stats.get('unique_domains', 0)):,}"],
            ['Organizations with Domains', f"{int(stats.get('organizations_with_domains', 0)):,}"]
        ]
        
        org_table = Table(org_table_data, colWidths=[4.5*inch, 2.5*inch])
        org_table.setStyle(TableStyle([
            # Header row với purple
            ('BACKGROUND', (0, 0), (-1, 0), cosigma_purple),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 0), (-1, 0), 12),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            # Data rows
            ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
            ('TOPPADDING', (0, 1), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 10),
            ('LEFTPADDING', (0, 0), (-1, -1), 12),
            ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ]))
        elements.append(KeepTogether(org_table))
        elements.append(Spacer(1, 0.25*inch))
        
        # Top Domain Occurrences với orange header - đảm bảo title và table cùng trang
        if stats.get('domain_occurrences'):
            domain_title = Paragraph("Top Domain Occurrences", heading_style)
            domain_data = [['Domain', 'Count']]
            sorted_domains = sorted(
                stats['domain_occurrences'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:15]  # Top 15 for PDF
            for domain, count in sorted_domains:
                domain_data.append([domain[:45] + ('...' if len(domain) > 45 else ''), f"{int(count):,}"])
            
            domain_table = Table(domain_data, colWidths=[4.5*inch, 2.5*inch])
            domain_table.setStyle(TableStyle([
                # Header row với orange
                ('BACKGROUND', (0, 0), (-1, 0), cosigma_orange),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                # Data rows
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (1, 1), (1, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TEXTCOLOR', (0, 1), (-1, -1), cosigma_dark),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, cosigma_light_gray]),
                ('TOPPADDING', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ]))
            # Giữ title và table cùng trang bằng KeepTogether
            elements.append(KeepTogether([domain_title, domain_table]))
            elements.append(Spacer(1, 0.25*inch))
        
        # Build PDF với header và footer trên mỗi trang
        doc.build(elements, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
        buffer.seek(0)
        
        filename = f"mongodb_report_{period}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error exporting PDF: {e}", exc_info=True)
        error_msg = str(e)
        if "styles" in error_msg.lower():
            error_msg = f"Styles initialization error: {error_msg}"
        return jsonify({'success': False, 'error': error_msg}), 500


def _generate_report(period, hours=None, days=None):
    """Helper function to generate reports - reduces code duplication."""
    try:
        now = datetime.now(timezone.utc)
        if hours:
            start = now - timedelta(hours=hours)
        elif days:
            start = now - timedelta(days=days)
        else:
            raise ValueError("Either hours or days must be provided")
        
        end = now
        
        stats = get_stats_from_db(start, end)
        org_stats = get_organizations_stats()
        timestamps = get_data_timestamps()
        
        return jsonify({
            'success': True,
            'period': period,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'stats': {**stats, **org_stats},
            'dated': timestamps.get('dated', {})
        })
    except Exception as e:
        logger.error(f"Error generating {period} report: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/report/daily')
def daily_report():
    """Generate daily report (last 24 hours)."""
    logger.info("API /api/report/daily endpoint called")
    return _generate_report('daily', hours=24)


@app.route('/api/report/weekly')
def weekly_report():
    """Generate weekly report (last 7 days)."""
    logger.info("API /api/report/weekly endpoint called")
    return _generate_report('weekly', days=7)


@app.route('/api/search/domain')
def search_domain():
    """Search for domains in organizations. Returns organizations containing matching domains."""
    logger.info("API /api/search/domain endpoint called")
    try:
        domain_query = request.args.get('q', '').strip()
        try:
            limit = int(request.args.get('limit', 50))
            limit = max(1, min(limit, 100))  # Clamp between 1 and 100
        except (ValueError, TypeError):
            limit = 50
        
        if not domain_query:
            return jsonify({'success': False, 'error': 'Domain query parameter (q) is required'}), 400
        
        org_col = get_collection('organizations')
        
        # Search for organizations containing the domain (case-insensitive partial match)
        pipeline = [
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": True}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""},
                "domains": {"$regex": domain_query, "$options": "i"}  # Case-insensitive search
            }},
            {"$group": {
                "_id": "$_id",
                "display_name": {"$first": "$display_name"},
                "all_domains": {"$push": "$domains"},
                "matching_domains": {"$push": "$domains"},
                "created_at": {"$first": "$created_at"},
                "updated_at": {"$first": "$updated_at"}
            }},
            {"$limit": limit}
        ]
        
        results = list(org_col.aggregate(pipeline, allowDiskUse=True))
        
        # Format results - filter matching domains
        formatted_results = []
        for org in results:
            all_domains = org.get('all_domains', [])
            matching_domains = [d for d in all_domains if domain_query.lower() in d.lower()]
            # Handle dates - can be datetime object or string
            created_at = _format_date_safe(org.get('created_at'))
            updated_at = _format_date_safe(org.get('updated_at'))
            
            formatted_results.append({
                'organization_index': str(org['_id']),
                'display_name': org.get('display_name', 'N/A'),
                'matching_domains': matching_domains,
                'all_domains': all_domains,
                'total_domains': len(all_domains),
                'matching_count': len(matching_domains),
                'created_at': created_at,
                'updated_at': updated_at
            })
        
        return jsonify({
            'success': True,
            'query': domain_query,
            'total_organizations': len(formatted_results),
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error searching domain: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/search/organization')
def search_organization():
    """Search for organizations by index or display name."""
    logger.info("API /api/search/organization endpoint called")
    try:
        org_query = request.args.get('q', '').strip()
        try:
            limit = int(request.args.get('limit', 50))
            limit = max(1, min(limit, 100))  # Clamp between 1 and 100
        except (ValueError, TypeError):
            limit = 50
        
        if not org_query:
            return jsonify({'success': False, 'error': 'Organization query parameter (q) is required'}), 400
        
        org_col = get_collection('organizations')
        
        # Try to match by _id first, then by display_name
        query = {
            "$or": [
                {"_id": {"$regex": org_query, "$options": "i"}},
                {"display_name": {"$regex": org_query, "$options": "i"}}
            ]
        }
        
        results = list(org_col.find(query).limit(limit))
        
        # Format results
        formatted_results = []
        for org in results:
            domains = org.get('domains', [])
            
            # Handle dates - can be datetime object or string
            created_at = _format_date_safe(org.get('created_at'))
            updated_at = _format_date_safe(org.get('updated_at'))
            
            formatted_results.append({
                'organization_index': str(org['_id']),
                'display_name': org.get('display_name', 'N/A'),
                'domains': domains,
                'total_domains': len(domains) if isinstance(domains, list) else 0,
                'created_at': created_at,
                'updated_at': updated_at
            })
        
        return jsonify({
            'success': True,
            'query': org_query,
            'total_found': len(formatted_results),
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error searching organization: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/domain/indexes')
def get_domain_indexes():
    """Get all organization indexes that contain a specific domain."""
    logger.info("API /api/domain/indexes endpoint called")
    try:
        domain = request.args.get('domain', '').strip()
        
        if not domain:
            return jsonify({'success': False, 'error': 'Domain parameter is required'}), 400
        
        org_col = get_collection('organizations')
        
        # Find all organizations containing this domain
        pipeline = [
            {"$match": {"domains": domain}},
            {"$project": {
                "_id": 1,
                "display_name": 1,
                "domains": 1,
                "created_at": 1,
                "updated_at": 1
            }}
        ]
        
        results = list(org_col.aggregate(pipeline, allowDiskUse=True))
        
        # Format results
        formatted_results = []
        for org in results:
            # Handle dates - can be datetime object or string
            created_at = _format_date_safe(org.get('created_at'))
            updated_at = _format_date_safe(org.get('updated_at'))
            
            formatted_results.append({
                'organization_index': str(org['_id']),
                'display_name': org.get('display_name', 'N/A'),
                'domain': domain,
                'all_domains': org.get('domains', []),
                'created_at': created_at,
                'updated_at': updated_at
            })
        
        return jsonify({
            'success': True,
            'domain': domain,
            'total_organizations': len(formatted_results),
            'organization_indexes': [r['organization_index'] for r in formatted_results],
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error getting domain indexes: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alerts/domains/count')
def count_alerts_domains():
    """Count domain occurrences in alerts collection (backoffice/alert/domains).
    
    Since domains is an array, uses $unwind to count each domain occurrence.
    Supports filtering by domain pattern using $regex and time period based on updated_date.
    """
    logger.info("API /api/alerts/domains/count endpoint called")
    try:
        alerts_col = get_collection('alerts')
        
        # Get optional query parameters
        domain_pattern = request.args.get('domain', '').strip()
        period = request.args.get('period', 'weekly')  # daily or weekly
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        try:
            limit = int(request.args.get('limit', 100))
            limit = max(1, min(limit, 500))  # Clamp between 1 and 500
        except (ValueError, TypeError):
            limit = 100
        
        # Parse date range for updated_date filter (alerts uses updated_date)
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0 (parse_date_range already normalizes, but be explicit)
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        # Build aggregation pipeline
        pipeline = [
            # First filter by updated_date (time period filter) - alerts collection
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            # Unwind domains array to get individual domain values
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            # Filter out null/empty domains
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        # Add domain pattern filter if provided
        if domain_pattern:
            pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}  # Case-insensitive regex
                }
            })
        
        # Group by domain and count occurrences
        pipeline.extend([
            {"$group": {
                "_id": "$domains",
                "count": {"$sum": 1},
                # Also get sample document IDs for reference
                "sample_doc_ids": {"$push": "$_id"}
            }},
            {"$project": {
                "_id": 0,
                "domain": "$_id",
                "count": 1,
                "sample_doc_ids": {"$slice": ["$sample_doc_ids", 5]}  # First 5 doc IDs as samples
            }},
            {"$sort": {"count": -1}},  # Sort by count descending
            {"$limit": limit}
        ])
        
        results = list(alerts_col.aggregate(pipeline, allowDiskUse=True))
        
        # Calculate total unique domains and total occurrences
        total_pipeline = [
            # First filter by updated_date (time period filter) - alerts collection
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        # Add domain pattern filter if provided
        if domain_pattern:
            total_pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}
                }
            })
        
        total_pipeline.append({
            "$facet": {
                "total_occurrences": [
                    {"$count": "count"}
                ],
                "unique_domains": [
                    {"$group": {"_id": "$domains"}},
                    {"$count": "count"}
                ]
            }
        })
        
        total_result = list(alerts_col.aggregate(total_pipeline, allowDiskUse=True))
        
        total_occurrences = 0
        unique_domains = 0
        
        if total_result and total_result[0]:
            facets = total_result[0]
            total_occurrences = facets.get('total_occurrences', [{}])[0].get('count', 0) if facets.get('total_occurrences') else 0
            unique_domains = facets.get('unique_domains', [{}])[0].get('count', 0) if facets.get('unique_domains') else 0
        
        return jsonify({
            'success': True,
            'collection': 'alerts',
            'date_field': 'updated_date',
            'domain_pattern': domain_pattern if domain_pattern else None,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_occurrences': total_occurrences,
            'unique_domains': unique_domains,
            'total_returned': len(results),
            'results': results
        })
    except Exception as e:
        logger.error(f"Error counting alerts domains: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/archives/domains/count')
def count_archives_domains():
    """Count domain occurrences in archives collection (archives/archives).
    
    Note: Archives collection may not have domains field directly.
    This endpoint is prepared for future use if archives have domain-related data.
    Uses inserted_time for time period filtering.
    """
    logger.info("API /api/archives/domains/count endpoint called")
    try:
        archives_col = get_collection('archives')
        
        # Get optional query parameters
        domain_pattern = request.args.get('domain', '').strip()
        period = request.args.get('period', 'weekly')  # daily or weekly
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        try:
            limit = int(request.args.get('limit', 100))
            limit = max(1, min(limit, 500))
        except (ValueError, TypeError):
            limit = 100
        
        # Parse date range for inserted_time filter (archives uses inserted_time)
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0 (parse_date_range already normalizes, but be explicit)
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        # Check if archives collection has domains field
        sample_doc = archives_col.find_one({}, {"domains": 1})
        has_domains = sample_doc and 'domains' in sample_doc
        
        if not has_domains:
            return jsonify({
                'success': True,
                'collection': 'archives',
                'date_field': 'inserted_time',
                'message': 'Archives collection does not have domains field',
                'period': period,
                'start_date': start.isoformat(),
                'end_date': end.isoformat(),
                'total_occurrences': 0,
                'unique_domains': 0,
                'total_returned': 0,
                'results': []
            })
        
        # Build aggregation pipeline
        pipeline = [
            # First filter by inserted_time (time period filter) - archives collection
            {"$match": {
                "inserted_time": {"$gte": start, "$lte": end}
            }},
            # Unwind domains array if it exists
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        if domain_pattern:
            pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}
                }
            })
        
        pipeline.extend([
            {"$group": {
                "_id": "$domains",
                "count": {"$sum": 1},
                "sample_doc_ids": {"$push": "$_id"}
            }},
            {"$project": {
                "_id": 0,
                "domain": "$_id",
                "count": 1,
                "sample_doc_ids": {"$slice": ["$sample_doc_ids", 5]}
            }},
            {"$sort": {"count": -1}},
            {"$limit": limit}
        ])
        
        results = list(archives_col.aggregate(pipeline, allowDiskUse=True))
        
        # Calculate totals
        total_pipeline = [
            {"$match": {
                "inserted_time": {"$gte": start, "$lte": end}
            }},
            {"$unwind": {"path": "$domains", "preserveNullAndEmptyArrays": False}},
            {"$match": {
                "domains": {"$ne": None, "$ne": ""}
            }}
        ]
        
        if domain_pattern:
            total_pipeline.append({
                "$match": {
                    "domains": {"$regex": domain_pattern, "$options": "i"}
                }
            })
        
        total_pipeline.append({
            "$facet": {
                "total_occurrences": [{"$count": "count"}],
                "unique_domains": [
                    {"$group": {"_id": "$domains"}},
                    {"$count": "count"}
                ]
            }
        })
        
        total_result = list(archives_col.aggregate(total_pipeline, allowDiskUse=True))
        
        total_occurrences = 0
        unique_domains = 0
        
        if total_result and total_result[0]:
            facets = total_result[0]
            total_occurrences = facets.get('total_occurrences', [{}])[0].get('count', 0) if facets.get('total_occurrences') else 0
            unique_domains = facets.get('unique_domains', [{}])[0].get('count', 0) if facets.get('unique_domains') else 0
        
        return jsonify({
            'success': True,
            'collection': 'archives',
            'date_field': 'inserted_time',
            'domain_pattern': domain_pattern if domain_pattern else None,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_occurrences': total_occurrences,
            'unique_domains': unique_domains,
            'total_returned': len(results),
            'results': results
        })
    except Exception as e:
        logger.error(f"Error counting archives domains: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/alerts/domains/search')
def search_alerts_domain():
    """Search for a specific domain in alerts collection using regex pattern.
    
    Supports time period filtering based on updated_date.
    Example: /api/alerts/domains/search?domain=cosigma.io&period=daily
    """
    logger.info("API /api/alerts/domains/search endpoint called")
    try:
        domain_query = request.args.get('domain', '').strip()
        period = request.args.get('period', 'weekly')  # daily or weekly
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        try:
            limit = int(request.args.get('limit', 50))
            limit = max(1, min(limit, 200))  # Clamp between 1 and 200
        except (ValueError, TypeError):
            limit = 50
        
        if not domain_query:
            return jsonify({'success': False, 'error': 'Domain query parameter (domain) is required'}), 400
        
        alerts_col = get_collection('alerts')
        
        # Parse date range for updated_date filter
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0 (parse_date_range already normalizes, but be explicit)
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        # Search for alerts containing the domain (case-insensitive regex match)
        pipeline = [
            # First filter by updated_date (time period filter)
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            {"$match": {
                "domains": {"$regex": domain_query, "$options": "i"}  # Case-insensitive regex
            }},
            {"$project": {
                "_id": 1,
                "domains": 1,
                "created_date": 1,
                "updated_date": 1,
                "organization_id": 1,
                "type": 1,
                "full_url": 1,
                "normalized_url": 1
            }},
            {"$limit": limit}
        ]
        
        results = list(alerts_col.aggregate(pipeline, allowDiskUse=True))
        
        # Count total matching documents
        count_pipeline = [
            # First filter by updated_date (time period filter)
            {"$match": {
                "updated_date": {"$gte": start, "$lte": end}
            }},
            {"$match": {
                "domains": {"$regex": domain_query, "$options": "i"}
            }},
            {"$count": "total"}
        ]
        
        count_result = list(alerts_col.aggregate(count_pipeline, allowDiskUse=True))
        total_count = count_result[0]['total'] if count_result else 0
        
        # Format results
        formatted_results = []
        for alert in results:
            domains = alert.get('domains', [])
            matching_domains = [d for d in domains if domain_query.lower() in d.lower()]
            
            formatted_results.append({
                'alert_id': str(alert['_id']),
                'matching_domains': matching_domains,
                'all_domains': domains,
                'organization_id': alert.get('organization_id'),
                'type': alert.get('type'),
                'created_date': _format_date_safe(alert.get('created_date')),
                'updated_date': _format_date_safe(alert.get('updated_date')),
                'full_url': alert.get('full_url'),
                'normalized_url': alert.get('normalized_url')
            })
        
        return jsonify({
            'success': True,
            'query': domain_query,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'total_matching_alerts': total_count,
            'returned_count': len(formatted_results),
            'results': formatted_results
        })
    except Exception as e:
        logger.error(f"Error searching alerts domain: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hwid/list')
def list_hwids():
    """List unique HWIDs from detections array with optional filtering.
    
    Returns list of unique HWIDs found in the specified time period.
    Uses same logic as build_hwid_pipeline: unwinds detections array and counts unique IDs.
    """
    logger.info(f"API /api/hwid/list endpoint called")
    try:
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        hwid_pattern = request.args.get('hwid_pattern', '').strip()
        limit = int(request.args.get('limit', 100))
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        alerts_col = get_collection('alerts')
        
        # Build pipeline to get unique HWIDs (similar to build_hwid_pipeline but returns list)
        pipeline = [
            # Step 1: Match date range and ensure detections exists and is array
            {"$match": {
                "created_date": {"$gte": start, "$lte": end},
                "detections": {"$exists": True, "$ne": None, "$type": "array", "$not": {"$size": 0}}
            }},
            # Step 2: Unwind detections array
            {"$unwind": "$detections"},
            # Step 3: Extract id from various possible paths
            {"$project": {
                "hwid": {
                    "$ifNull": [
                        "$detections.host.id",
                        {"$ifNull": [
                            "$detections.source.host.id",
                            None
                        ]}
                    ]
                },
                "alert_id": "$_id",
                "created_date": 1
            }},
            # Step 4: Filter out documents without hwid
            {"$match": {
                "hwid": {"$exists": True, "$ne": None, "$ne": "", "$type": "string"}
            }}
        ]
        
        # Add HWID pattern filter if provided (before grouping)
        if hwid_pattern:
            pipeline.append({
                "$match": {
                    "hwid": {"$regex": hwid_pattern, "$options": "i"}
                }
            })
        
        # Group by hwid to get unique IDs and collect alert info
        pipeline.extend([
            {"$group": {
                "_id": "$hwid",
                "alert_ids": {"$addToSet": "$alert_id"},
                "first_seen": {"$min": "$created_date"},
                "last_seen": {"$max": "$created_date"},
                "occurrence_count": {"$sum": 1}
            }},
            # Sort alphabetically
            {"$sort": {"_id": 1}},
            # Limit results
            {"$limit": limit}
        ])
        
        results = list(alerts_col.aggregate(pipeline, allowDiskUse=True))
        
        # Format results
        hwid_list = []
        for item in results:
            hwid_list.append({
                'hwid': item['_id'],
                'alert_count': len(item.get('alert_ids', [])),
                'occurrence_count': item.get('occurrence_count', 0),
                'first_seen': _format_date_safe(item.get('first_seen')),
                'last_seen': _format_date_safe(item.get('last_seen'))
            })
        
        return jsonify({
            'success': True,
            'period': period,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'hwid_pattern': hwid_pattern if hwid_pattern else None,
            'total_unique_hwids': len(hwid_list),
            'hwids': hwid_list
        })
    except Exception as e:
        logger.error(f"Error listing HWIDs: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/debug/archives')
def debug_archives():
    """Debug endpoint to test archives query directly - matches script count_zip_import_daily.py."""
    try:
        from db_helpers import get_collection
        from datetime import datetime, timedelta, timezone
        
        period = request.args.get('period', 'daily')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        start, end = parse_date_range(period, start_date, end_date)
        
        # Ensure UTC 0
        start = normalize_to_utc(start)
        end = normalize_to_utc(end)
        
        archives_col = get_collection('archives')
        
        # Test 1: Direct count query EXACTLY like script count_zip_import_daily.py
        # Script: query = {"inserted_time": {"$gte": one_day_ago}}
        script_query = {"inserted_time": {"$gte": start}}
        direct_count_script = archives_col.count_documents(script_query)
        
        # Test 2: Using aggregation pipeline (like dashboard)
        from db_helpers import build_count_pipeline
        zip_pipeline = build_count_pipeline('inserted_time', start, end, None, use_gte_only=True)
        zip_result = list(archives_col.aggregate(zip_pipeline, allowDiskUse=True))
        aggregation_count = zip_result[0]['total'] if zip_result else 0
        
        # Test 3: Get ALL documents with $gte (no limit to see all)
        all_docs = list(archives_col.find({
            "inserted_time": {"$gte": start}
        }).sort("inserted_time", -1).limit(10))
        
        # Test 4: Get current time and calculate one_day_ago exactly like script
        now_script_style = datetime.now(timezone.utc)
        one_day_ago_script_style = now_script_style - timedelta(hours=24)
        script_style_count = archives_col.count_documents({
            "inserted_time": {"$gte": one_day_ago_script_style}
        })
        
        return jsonify({
            'success': True,
            'query_info': {
                'period': period,
                'start_from_parse': start.isoformat(),
                'end_from_parse': end.isoformat(),
                'now_script_style': now_script_style.isoformat(),
                'one_day_ago_script_style': one_day_ago_script_style.isoformat()
            },
            'counts': {
                'direct_count_script_query': direct_count_script,
                'aggregation_pipeline_count': aggregation_count,
                'script_style_count': script_style_count
            },
            'pipeline_used': zip_pipeline,
            'sample_documents': [
                {
                    '_id': str(doc.get('_id')),
                    'inserted_time': doc.get('inserted_time').isoformat() if isinstance(doc.get('inserted_time'), datetime) else str(doc.get('inserted_time')),
                    'inserted_time_type': str(type(doc.get('inserted_time'))),
                    'is_decompressed': doc.get('is_decompressed', False)
                }
                for doc in all_docs
            ]
        })
    except Exception as e:
        logger.error(f"Error in debug endpoint: {e}", exc_info=True)
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


if __name__ == '__main__':
    import warnings
    # Suppress threading warnings on Windows
    warnings.filterwarnings('ignore', category=RuntimeWarning, module='werkzeug')
    
    try:
        # On Windows, disable reloader to avoid threading issues
        import platform
        use_reloader = platform.system() != 'Windows'
        
        # Log all registered routes
        print("\n" + "="*60)
        print("Flask Application Started")
        print("="*60)
        print(f"Registered Routes ({len(list(app.url_map.iter_rules()))}):")
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            if not rule.rule.startswith('/static'):
                print(f"  {rule.rule:40} -> {rule.endpoint}")
        print("="*60)
        print(f"Server running on http://0.0.0.0:5000")
        print(f"Access dashboard at http://localhost:5000")
        print("="*60 + "\n")
        
        app.run(
            debug=True, 
            host='0.0.0.0', 
            port=5000, 
            use_reloader=use_reloader,
            use_debugger=True
        )
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        print(f"Error starting server: {e}")
        import traceback
        traceback.print_exc()
