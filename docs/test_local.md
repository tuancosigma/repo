# Test trên Máy Local

## Đã hoàn thành ✅

1. **Setup Cache Collection**
   ```bash
   python tools/setup_cache.py
   ```
   ✓ TTL index đã được tạo

2. **Cache Ingestion**
   ```bash
   python tools/cache_ingest.py
   ```
   ✓ Đã ingest 4 documents vào cache:
   - stats_daily_20260309
   - chart_daily_20260309  
   - stats_weekly_20260303
   - chart_weekly_20260303

3. **Verify Cache**
   ```bash
   python verify_cache.py
   ```
   ✓ Cache có 4 documents
   ✓ Expires sau 10 ngày (2026-03-20)

4. **Dashboard Started**
   ```bash
   python app.py
   ```
   ✓ Dashboard đang chạy tại http://localhost:5000

## Test Dashboard

### 1. Mở Browser:
```
http://localhost:5000
```

### 2. Test API Endpoints:

**Daily Stats (từ cache):**
```
http://localhost:5000/api/stats?period=daily
```

**Weekly Stats (từ cache):**
```
http://localhost:5000/api/stats?period=weekly
```

**Chart Data:**
```
http://localhost:5000/api/chart-data?period=daily
http://localhost:5000/api/chart-data?period=weekly
```

### 3. Kiểm tra Cache hoạt động:

API response sẽ có field `"cached": true` nếu đọc từ cache:
```json
{
  "success": true,
  "stats": {...},
  "cached": true  ← Đọc từ cache
}
```

Nếu `"cached": false` → Đọc trực tiếp từ DB (fallback)

## Test Cache Refresh

1. **Chạy lại cache ingestion:**
   ```bash
   python tools/cache_ingest.py
   ```

2. **Verify cache được update:**
   ```bash
   python verify_cache.py
   ```

3. **Test API lại** - Data sẽ được refresh

## Next Steps

Sau khi test thành công trên local:

1. Upload code lên server
2. Chạy `tools/setup_cache.py` trên server
3. Setup cronjob để tự động ingest
4. Deploy dashboard với Gunicorn

Xem `DEPLOYMENT.md` để biết chi tiết.
