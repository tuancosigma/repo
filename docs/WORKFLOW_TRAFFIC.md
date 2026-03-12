# Workflow Traffic - Cosigma MongoDB Statistics Dashboard

## Tổng quan hệ thống

Hệ thống MongoDB Statistics Dashboard của Cosigma được thiết kế để theo dõi và phân tích dữ liệu từ các MongoDB collections với hệ thống cache tối ưu hiệu suất.

## Luồng dữ liệu (Data Flow)

### 1. Data Ingestion (Thu thập dữ liệu)

```
Source Collections (MongoDB)
├── archives.archives          → Zip archives được import
├── infostealer.credentials    → Credentials được harvest
├── backoffice.alerts          → Alerts với HWID detection
└── backoffice.organizations   → Organizations và domains
```

**Cronjob Process (`tools/cache_ingest.py`):**
- Chạy định kỳ (khuyến nghị: mỗi giờ)
- Copy raw documents từ source collections vào cache collections
- Chỉ lưu trữ dữ liệu trong 10 ngày gần nhất
- Tự động xóa dữ liệu cũ hơn 10 ngày (TTL index)

### 2. Cache System (Hệ thống Cache)

```
Cache Database (cache)
├── cache.archives      → Raw archive documents (10 days TTL)
├── cache.credentials   → Raw credential documents (10 days TTL)
├── cache.alerts        → Raw alert documents (10 days TTL)
└── cache.stats        → Aggregated statistics (10 days TTL)
    ├── stats_daily_YYYYMMDD    → Daily aggregated stats
    ├── chart_daily_YYYYMMDD     → Daily chart data
    ├── stats_weekly_YYYYMMDD    → Weekly aggregated stats
    └── chart_weekly_YYYYMMDD    → Weekly chart data
```

**Cache Features:**
- **TTL Index**: Tự động xóa documents sau 10 ngày
- **Raw Data**: Lưu toàn bộ documents gốc (không chỉ aggregated)
- **Time Ingest**: Ghi nhận thời gian cache (`cached_at`)
- **Expiration**: Mỗi document có `expires_at` = now + 10 days

### 3. Dashboard Request Flow (Luồng yêu cầu Dashboard)

```
User Browser
    ↓
Flask Web App (app.py)
    ↓
API Endpoint (/api/stats)
    ↓
Check Cache First?
    ├── YES → Get from cache.stats (fast)
    └── NO  → Query cache collections (archives, credentials, alerts)
              └── Fallback → Query source collections
    ↓
Return JSON Response
    ↓
Frontend (dashboard.js)
    ↓
Display Statistics & Charts
```

**Performance Optimization:**
1. **Cache First**: Luôn kiểm tra cache trước
2. **Parallel Queries**: Sử dụng aggregation pipelines
3. **Connection Pooling**: Reuse MongoDB connections
4. **Lazy Loading**: Charts chỉ load khi cần

### 4. Cache Ingest Workflow (Quy trình Cache Ingestion)

```
Cronjob Trigger (hourly)
    ↓
1. Cleanup Expired Cache
   └── Remove documents older than 10 days
    ↓
2. Copy Raw Data (last 10 days)
   ├── archives → cache.archives
   ├── credentials → cache.credentials
   └── alerts → cache.alerts
   └── Each document gets: expires_at, cached_at
    ↓
3. Aggregate Statistics
   ├── Daily stats (last 24h)
   └── Weekly stats (last 7 days)
    ↓
4. Store in cache.stats
   └── With TTL expiration
```

## Data Collections Mapping

| Source Collection | Cache Collection | Date Field | Purpose |
|-------------------|------------------|------------|---------|
| `archives.archives` | `cache.archives` | `inserted_time` | Zip archive imports |
| `infostealer.credentials` | `cache.credentials` | `harvest_date` | Credential harvesting |
| `backoffice.alerts` | `cache.alerts` | `created_date` | HWID detections |
| `backoffice.organizations` | N/A (no cache) | `created_at/updated_at` | Organization stats |

## API Endpoints Flow

### `/api/stats`
```
Request → Parse date range → Check cache.stats → 
  ├── Found → Return cached stats + org stats
  └── Not Found → Query cache collections → 
      └── Fallback to source collections
```

### `/api/chart-data`
```
Request → Parse date range → Check cache.stats → 
  ├── Found → Return cached chart data
  └── Not Found → Query collections with intervals → 
      └── Aggregate by time intervals
```

### `/api/export-pdf` & `/api/export-csv`
```
Request → Get stats from DB → Get organizations stats → 
  └── Format and return report (PDF/CSV)
```

## Performance Characteristics

### Cache Hit (Fast Path)
- **Response Time**: < 100ms
- **Data Source**: Pre-aggregated cache
- **Database Queries**: 1-2 queries

### Cache Miss (Slow Path)
- **Response Time**: 500ms - 2s
- **Data Source**: Raw cache collections hoặc source collections
- **Database Queries**: 4-8 aggregation queries

### Cache Ingest (Background)
- **Duration**: 1-5 minutes (tùy data volume)
- **Frequency**: Hourly (recommended)
- **Impact**: Minimal (runs in background)

## TTL và Expiration Logic

```
Document Lifecycle:
1. Created in source collection
2. Copied to cache (expires_at = now + 10 days)
3. TTL index automatically deletes when expires_at < current_time
4. Cache ingest updates expires_at for existing documents
```

**Key Points:**
- Cache chỉ lưu **10 ngày gần nhất**
- Documents tự động expire sau 10 ngày
- Cache ingest chạy định kỳ để refresh data

## Monitoring và Maintenance

### Cache Health Check
- Use `/api/cache-info` endpoint
- Check `valid_documents` vs `expired_documents`
- Monitor `cached_at` timestamps

### Performance Monitoring
- Log cache hit/miss rates
- Monitor query response times
- Track cache ingest duration

### Maintenance Tasks
- Regular cache ingest (cronjob)
- Monitor TTL index performance
- Check cache collection sizes

## Best Practices

1. **Cache Ingest**: Chạy mỗi giờ để đảm bảo data fresh
2. **Dashboard Usage**: Sử dụng period selector (daily/weekly) để tận dụng cache
3. **Monitoring**: Kiểm tra cache health định kỳ
4. **Performance**: Cache collections có index trên date fields và expires_at

## Troubleshooting

### Cache không có data
- Kiểm tra cronjob có chạy không
- Verify cache ingest script
- Check MongoDB connection

### Performance chậm
- Kiểm tra cache hit rate
- Verify indexes exist
- Check data volume

### Data không cập nhật
- Verify cache ingest frequency
- Check expires_at timestamps
- Ensure TTL index is active
