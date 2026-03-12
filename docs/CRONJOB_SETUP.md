# Cronjob Setup Guide

Hướng dẫn thiết lập cronjob để tự động ingest data vào cache collection.

## Bước 1: Setup Cache Collection

Chạy script để tạo TTL index (tự động xóa sau 10 ngày):

```bash
python tools/setup_cache.py
```

## Bước 2: Test Cache Ingestion

Chạy thử script ingest để kiểm tra:

```bash
python tools/cache_ingest.py
```

## Bước 3: Setup Cronjob

### Linux/Unix:

Mở crontab:
```bash
crontab -e
```

Thêm dòng sau (chạy mỗi 5 phút):
```bash
*/5 * * * * cd /path/to/infra && /usr/bin/python3 tools/cache_ingest.py >> /var/log/cache_ingest.log 2>&1
```

Hoặc chạy mỗi phút (để test):
```bash
* * * * * cd /path/to/infra && /usr/bin/python3 tools/cache_ingest.py >> /var/log/cache_ingest.log 2>&1
```

### Windows (Task Scheduler):

1. Mở Task Scheduler
2. Create Basic Task
3. Trigger: Every 5 minutes
4. Action: Start a program
5. Program: `python`
6. Arguments: `tools/cache_ingest.py`
7. Start in: `C:\path\to\infra` (thay bằng đường dẫn thực tế của project)

### Docker (nếu dùng Docker):

Thêm vào docker-compose.yml:
```yaml
services:
  cache-ingest:
    image: python:3.11
    volumes:
      - ./:/app
    working_dir: /app
    command: >
      sh -c "pip install -r requirements.txt &&
             python tools/cache_ingest.py"
    restart: "on-failure"
    environment:
      - MONGODB_URL=${MONGODB_URL}
    depends_on:
      - mongodb
```

## Bước 4: Verify

Kiểm tra cache collection:
```bash
python -c "
from pymongo import MongoClient
from dotenv import load_dotenv
import os
load_dotenv()
client = MongoClient(os.getenv('MONGODB_URL'))
cache = client['cache']['stats']
print('Cache documents:', cache.count_documents({}))
for doc in cache.find().limit(5):
    print(f\"  {doc['_id']}: expires_at={doc['expires_at']}\")
"
```

## Lưu ý

- Cache sẽ tự động xóa sau 10 ngày (TTL index)
- Cronjob nên chạy mỗi 5 phút để đảm bảo data luôn fresh
- Logs sẽ được ghi vào `/var/log/cache_ingest.log` (Linux)
- Đảm bảo Python path đúng trong cronjob
