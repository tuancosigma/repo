# Deployment Guide - Production Server

Hướng dẫn deploy hệ thống lên server production.

## Bước 1: Upload Code lên Server

### Option 1: Git Clone
```bash
# Trên server
cd ~/tuan
git clone https://github.com/cosigma-io/infra.git
cd infra
```

### Option 2: SCP/SFTP
```bash
# Từ máy local, upload toàn bộ folder
scp -r . user@server:~/tuan/infra/
```

### Option 3: Manual Copy
- Copy toàn bộ project lên server

## Bước 2: Setup Environment trên Server

```bash
# SSH vào server
ssh user@server

# Di chuyển vào thư mục
cd ~/tuan/infra

# Tạo file .env với MongoDB connection
nano .env
```

Thêm vào `.env`:
```env
MONGODB_URL=mongodb://your-mongodb-connection-string
MONGODB_TIMEOUT_MS=3000
MONGODB_MAX_POOL_SIZE=50
MONGODB_MIN_POOL_SIZE=5
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

## Bước 4: Chạy ứng dụng

### Option 1: Chạy trực tiếp (Development)
```bash
cd ~/tuan/infra
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
User=your-user
WorkingDirectory=/path/to/infra
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

## Bước 5: Setup Nginx Reverse Proxy (Optional)

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

## Bước 6: Verify Everything

### Test Dashboard:
```bash
# Test API endpoint
curl http://localhost:5000/api/stats?period=daily

# Hoặc mở browser
# http://your-server-ip:5000
```

### Monitor Performance:
```bash
# Check process
ps aux | grep python

# Check logs
tail -f /var/log/dashboard.log
```

## Troubleshooting

### Dashboard không start:
```bash
# Check port đã dùng chưa
sudo netstat -tulpn | grep 5000

# Check logs
tail -f /var/log/dashboard.log

# Check Python path
which python3
```

### MongoDB connection issues:
```bash
# Test MongoDB connection
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
/path/to/infra/
├── app.py                    # Flask dashboard
├── config.py                 # Configuration
├── db_helpers.py             # Database helpers
├── count_*.py               # CLI scripts
├── .env                     # Environment config
├── requirements.txt         # Dependencies
├── templates/              # HTML templates
├── static/                 # CSS, JS, images
└── docs/                   # Documentation
```

## Quick Commands Reference

```bash
# Restart dashboard service
sudo systemctl restart dashboard

# View logs
tail -f /var/log/dashboard.log

# Check service status
sudo systemctl status dashboard
```
