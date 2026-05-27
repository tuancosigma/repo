# MongoDB Statistics Dashboard

Dashboard web application để theo dõi và phân tích dữ liệu từ MongoDB collections với các tính năng tìm kiếm và báo cáo chuyên nghiệp.

## 🚀 Tính năng

### Dashboard Statistics
- **ZIP Archives Imported**: Đếm số lượng archives được import
- **Decompressed Archives**: Đếm số lượng archives đã giải nén
- **Credentials Found**: Đếm số lượng credentials được tìm thấy
- **HWID Found**: Đếm số lượng Hardware ID duy nhất từ detections array
- **Organizations**: Thống kê organizations và indexes
- **Total Domains**: Đếm tổng số domains và unique domains
- **Domain Occurrences**: Hiển thị các domains phổ biến nhất

### Search & Reports
- **Domain Search**: Tìm kiếm domains trong organizations với highlight
- **Organization Search**: Tìm kiếm organizations theo index hoặc tên
- **Alerts Domain Search**: Tìm kiếm alerts chứa domain cụ thể (regex support)
- **HWID Search**: Tìm kiếm và lọc Hardware IDs
- **Domain Indexes**: Xem các organization indexes chứa domain cụ thể
- **Domain Count**: Đếm domain occurrences trong archives và alerts

### Export Features
- **PDF Export**: Xuất báo cáo dạng PDF
- **CSV Export**: Xuất dữ liệu dạng CSV
- **JSON Export**: Xuất dữ liệu dạng JSON
- **Copy to Clipboard**: Sao chép dữ liệu
- **Print**: In báo cáo

### Advanced Features
- **Date Range Filtering**: Lọc theo khoảng thời gian (daily/weekly)
- **Highlight Search Terms**: Highlight từ khóa tìm kiếm trong kết quả
- **Filter Results**: Lọc kết quả ngay trong bảng
- **View Details**: Xem chi tiết từng item trong modal
- **Interactive Charts**: Biểu đồ tương tác với Chart.js
- **Dark Theme**: Giao diện dark theme hiện đại

## 📋 Yêu cầu

- Python 3.8+
- MongoDB 4.4+
- pip

## 🔧 Cài đặt

### 1. Clone repository

```bash
git clone https://github.com/cosigma-io/infra.git
cd infra
```

### 2. Cài đặt dependencies

```bash
pip install -r requirements.txt
```

### 3. Cấu hình Environment Variables

Tạo file `.env` trong thư mục gốc:

```env
MONGODB_URL=mongodb://localhost:27017
MONGODB_TIMEOUT_MS=3000
MONGODB_MAX_POOL_SIZE=50
MONGODB_MIN_POOL_SIZE=5
```

### 4. Chạy ứng dụng

```bash
python app.py
```

Dashboard sẽ chạy tại: `http://localhost:5000`

## 📁 Cấu trúc Project

```
.
├── app.py                 # Flask web application (main entry point)
├── config.py             # Configuration và settings
├── db_helpers.py          # Database helper functions
├── requirements.txt       # Python dependencies
├── .env                  # Environment variables (không commit)
├── .gitignore            # Git ignore rules
│
├── scripts/               # CLI count scripts
│   ├── count_zip_import_daily.py
│   ├── count_zip_import_weekly.py
│   ├── count_decompressed_daily.py
│   ├── count_decompressed_weekly.py
│   ├── count_credentials_daily.py
│   ├── count_credentials_weekly.py
│   ├── count_hwid_daily.py
│   ├── count_hwid_weekly.py
│   └── count_organizations.py
│
├── templates/            # HTML templates
│   └── dashboard.html    # Main dashboard page
│
├── static/               # Static files
│   ├── dashboard.js      # Dashboard JavaScript
│   └── style.css         # Dashboard styles
│
└── docs/                 # Documentation
    ├── DEPLOYMENT.md     # Hướng dẫn deploy
    └── PROJECT_STRUCTURE.md
```

## 🗄️ MongoDB Collections

Project sử dụng các collections sau:

- **archives.archives**: Zip archives được import
- **infostealer.credentials**: Credentials được harvest
- **backoffice.alerts**: Alerts với HWID detection
- **backoffice.organizations**: Organizations và domains

## ⚙️ Cấu hình

### Timezone Policy

Tất cả datetime operations sử dụng **UTC 0 (+00:00)**:
- Tất cả MongoDB queries sử dụng UTC datetime objects
- Frontend hiển thị dates ở UTC format
- Dates từ MongoDB được normalize về UTC 0

### Date Fields

- **archives**: `inserted_time`
- **credentials**: `harvest_date` (ISO string)
- **alerts**: `created_date`, `updated_date`
- **organizations**: `created_at`, `updated_at`

## 📊 API Endpoints

### Statistics
- `GET /api/stats` - Lấy statistics tổng hợp
- `GET /api/chart-data` - Lấy dữ liệu cho biểu đồ

### Reports
- `GET /api/report/daily` - Báo cáo hàng ngày
- `GET /api/report/weekly` - Báo cáo hàng tuần
- `GET /api/export-pdf` - Xuất PDF
- `GET /api/export-csv` - Xuất CSV

### Search
- `GET /api/search/domain` - Tìm kiếm domain
- `GET /api/search/organization` - Tìm kiếm organization
- `GET /api/alerts/domains/count` - Đếm domains trong alerts
- `GET /api/archives/domains/count` - Đếm domains trong archives
- `GET /api/alerts/domains/search` - Tìm kiếm alerts theo domain
- `GET /api/hwid/list` - Danh sách HWIDs
- `GET /api/domain/indexes` - Domain indexes

## 🛠️ Development

### Chạy scripts

```bash
# Daily counts
python scripts/count_zip_import_daily.py
python scripts/count_decompressed_daily.py
python scripts/count_credentials_daily.py
python scripts/count_hwid_daily.py

# Weekly counts
python scripts/count_zip_import_weekly.py
python scripts/count_decompressed_weekly.py
python scripts/count_credentials_weekly.py
python scripts/count_hwid_weekly.py
```


## 📝 License

Copyright © Breachunt

## 🔗 Links

- Repository: https://github.com/cosigma-io/infra
- Documentation: Xem thêm trong thư mục `docs/`
