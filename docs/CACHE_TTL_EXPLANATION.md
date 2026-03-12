# Cache TTL (Time-To-Live) Explanation

## Tổng quan

Cache sử dụng **TTL Index** của MongoDB để tự động xóa documents sau **10 ngày từ thời điểm hiện tại (NOW)**.

## Cách hoạt động

### 1. TTL Index Setup

```python
# Trong setup_cache.py
cache_coll.create_index(
    [("expires_at", ASCENDING)],
    expireAfterSeconds=0,  # Xóa khi expires_at < current_time
    name="idx_expires_at"
)
```

- `expireAfterSeconds=0` nghĩa là MongoDB sẽ xóa documents khi `expires_at < current_time`
- MongoDB tự động kiểm tra và xóa documents mỗi 60 giây (background task)

### 2. Cache Ingest Process

Mỗi lần chạy `cache_ingest.py`:

```python
now = datetime.now(timezone.utc)
expires_at = now + timedelta(days=10)  # 10 ngày từ BÂY GIỜ

# Mỗi document được set expires_at
doc_copy["expires_at"] = expires_at
doc_copy["cached_at"] = datetime.now(timezone.utc)
```

**Quan trọng:**
- `expires_at` được set = **NOW + 10 days** mỗi lần chạy
- Documents đã tồn tại sẽ được **update expires_at** (nhờ `replace_one` với `upsert=True`)
- Điều này đảm bảo documents luôn có thời gian sống 10 ngày từ thời điểm hiện tại

### 3. Auto-Deletion Logic

```
Current Time: 2026-03-10 07:30:00 UTC
Document expires_at: 2026-03-20 07:30:00 UTC
Status: VALID (expires_at > now)

Sau 10 ngày:
Current Time: 2026-03-20 07:30:01 UTC
Document expires_at: 2026-03-20 07:30:00 UTC
Status: EXPIRED (expires_at < now) → MongoDB tự động xóa
```

### 4. Cleanup Process

`cache_ingest.py` cũng có cleanup logic để xóa documents cũ hơn 10 ngày dựa trên date fields gốc:

```python
def cleanup_expired_cache():
    expire_threshold = now - timedelta(days=10)
    
    # Xóa documents dựa trên date fields gốc
    cache_archives.delete_many({
        "$or": [
            {"expires_at": {"$lt": now}},
            {"inserted_time": {"$lt": expire_threshold}}
        ]
    })
```

## Ví dụ Timeline

```
Day 0 (2026-03-10):
  - Document created với inserted_time = 2026-03-06
  - expires_at = 2026-03-20 (now + 10 days)
  - Status: VALID

Day 5 (2026-03-15):
  - cache_ingest.py chạy lại
  - expires_at được UPDATE = 2026-03-25 (now + 10 days)
  - Document được gia hạn thêm 10 ngày
  - Status: VALID

Day 10 (2026-03-20):
  - Nếu cache_ingest KHÔNG chạy
  - expires_at = 2026-03-20 < now (2026-03-20 07:30:01)
  - MongoDB TTL index tự động xóa document
  - Status: DELETED

Day 11 (2026-03-21):
  - Document đã bị xóa
  - Cache chỉ còn documents từ 10 ngày gần nhất
```

## Kết quả

✅ **Cache luôn chứa data từ 10 ngày gần nhất**
✅ **Documents tự động xóa sau 10 ngày**
✅ **Không cần manual cleanup**
✅ **TTL index đảm bảo performance tốt**

## Kiểm tra TTL

Chạy script để kiểm tra:

```bash
python test_ttl.py
```

Script sẽ hiển thị:
- Số lượng documents valid/expired
- Thời gian còn lại cho mỗi document
- Trạng thái TTL index

## Lưu ý

1. **MongoDB TTL task chạy mỗi 60 giây**, nên có thể có độ trễ nhỏ (< 1 phút) trước khi documents bị xóa
2. **Mỗi lần cache_ingest chạy**, nó sẽ update `expires_at` cho documents hiện có, gia hạn thêm 10 ngày
3. **Documents cũ hơn 10 ngày** (dựa trên date fields gốc) sẽ bị cleanup ngay lập tức khi cache_ingest chạy
