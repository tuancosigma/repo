# Deployment Guide - Production Server

Hướng dẫn deploy hệ thống lên server production.

## Bước 1: Upload Code lên Server

### Option 1: SCP/SFTP
```bash
# Từ máy local, upload toàn bộ folder
scp -r script_axilen user@axilens-preprod:~/tuan/
```

### Option 2: Git (nếu có repo)
```bash
# Trên server
cd ~/tuan
git clone <your-repo-url> script_axilen
```

### Option 3: Manual Copy
- Copy toàn bộ folder `script_axilen` lên server vào `~/tuan/`

## Bước 2: Setup Environment trên Server

```bash
# SSH vào server
ssh user@axilens-preprod

# Di chuyển vào thư mục
cd ~/tuan/script_axilen

# Copy .env.example thành .env (nếu chưa có)
cp .env.example .env

# Chỉnh sửa .env với MongoDB connection thực tế
nano .env
```

## Bước 3: Install Dependencies

```bash
# Cài đặt Python packages
pip3 install -r requirements.txt

# Hoặc nếu dùng virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Bước 4: Setup Cache Collection

```bash
# Chạy script setup cache với TTL index
python3 tools/setup_cache.py
```

Output mong đợi:
```
[OK] Connected to MongoDB
[OK] TTL index created on expires_at field
[OK] Cache retention: 10 days
```

## Bước 5: Test Cache Ingestion

```bash
# Chạy thử cache ingestion
python3 tools/cache_ingest.py
```

Kiểm tra kết quả:
```bash
# Verify cache data
python3 -c "
from pymongo import MongoClient
from dotenv import load_dotenv
import os
from datetime import datetime, timezone
load_dotenv()
client = MongoClient(os.getenv('MONGODB_URL'))
cache = client['cache']['stats']
print('Total cache documents:', cache.count_documents({}))
for doc in cache.find().limit(5):
    print(f\"  {doc['_id']}: expires_at={doc['expires_at']}\")
"
```

## Bước 6: Setup Cronjob

### Kiểm tra cronjob hiện tại:
```bash
crontab -l
```

### Thêm cronjob mới:
```bash
crontab -e
```

### Thêm dòng sau (chạy mỗi 5 phút):
```bash
*/5 * * * * cd /home/tuan/script_axilen && /usr/bin/python3 tools/cache_ingest.py >> /var/log/cache_ingest.log 2>&1
```

**Lưu ý:** Điều chỉnh đường dẫn Python và thư mục cho đúng với server của bạn.

### Verify cronjob:
```bash
# Xem lại cronjob đã thêm
crontab -l

# Check log sau vài phút
tail -f /var/log/cache_ingest.log
```

## Bước 7: Deploy Flask App (Dashboard)

### Option 1: Chạy trực tiếp (Development)
```bash
cd ~/tuan/script_axilen
python3 app.py
```

### Option 2: Dùng Gunicorn (Production)
```bash
# Cài Gunicorn
pip3 install gunicorn

# Chạy với Gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Hoặc chạy background
nohup gunicorn -w 4 -b 0.0.0.0:5000 app:app > /var/log/dashboard.log 2>&1 &
```

### Option 3: Systemd Service (Recommended)
Tạo file `/etc/systemd/system/dashboard.service`:
```ini
[Unit]
Description=MongoDB Dashboard Service
After=network.target

[Service]
User=tuan
WorkingDirectory=/home/tuan/script_axilen
Environment="PATH=/usr/bin:/usr/local/bin"
ExecStart=/usr/bin/gunicorn -w 4 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable và start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable dashboard
sudo systemctl start dashboard
sudo systemctl status dashboard
```

## Bước 8: Setup Nginx Reverse Proxy (Optional)

Tạo file `/etc/nginx/sites-available/dashboard`:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Enable và restart:
```bash
sudo ln -s /etc/nginx/sites-available/dashboard /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Bước 9: Verify Everything

### 1. Check Cache Ingestion:
```bash
# Xem log cronjob
tail -f /var/log/cache_ingest.log

# Check cache data
python3 -c "
from pymongo import MongoClient
from dotenv import load_dotenv
import os
load_dotenv()
client = MongoClient(os.getenv('MONGODB_URL'))
cache = client['cache']['stats']
print('Cache documents:', cache.count_documents({}))
"
```

### 2. Test Dashboard:
```bash
# Test API endpoint
curl http://localhost:5000/api/stats?period=daily

# Hoặc mở browser
# http://your-server-ip:5000
```

### 3. Monitor Performance:
```bash
# Check process
ps aux | grep python

# Check logs
tail -f /var/log/cache_ingest.log
tail -f /var/log/dashboard.log
```

## Troubleshooting

### Cronjob không chạy:
```bash
# Check cron service
sudo systemctl status cron

# Check cron logs
grep CRON /var/log/syslog

# Test manual
cd ~/tuan/script_axilen && python3 tools/cache_ingest.py
```

### Dashboard không start:
```bash
# Check port đã dùng chưa
sudo netstat -tulpn | grep 5000

# Check logs
tail -f /var/log/dashboard.log

# Check Python path
which python3
```

### Cache không update:
```bash
# Check MongoDB connection
python3 -c "
from pymongo import MongoClient
from dotenv import load_dotenv
import os
load_dotenv()
client = MongoClient(os.getenv('MONGODB_URL'))
client.admin.command('ping')
print('MongoDB connection OK')
"
```

## File Structure trên Server

```
~/tuan/script_axilen/
├── app.py                    # Flask dashboard
├── cache_ingest.py          # Cronjob script
├── setup_cache.py           # Setup script
├── setup_indexes.py         # Index setup
├── count_*.py               # CLI scripts
├── .env                     # Environment config
├── requirements.txt         # Dependencies
├── templates/              # HTML templates
├── static/                 # CSS, JS, images
└── public/                 # Logo
```

## Quick Commands Reference

```bash
# Run cache ingestion manually
cd ~/tuan/script_axilen && python3 tools/cache_ingest.py

# Check cache status
python3 -c "from pymongo import MongoClient; from dotenv import load_dotenv; import os; load_dotenv(); client = MongoClient(os.getenv('MONGODB_URL')); print('Cache docs:', client['cache']['stats'].count_documents({}))"

# View cronjob
crontab -l

# Restart dashboard service
sudo systemctl restart dashboard

# View logs
tail -f /var/log/cache_ingest.log
```
