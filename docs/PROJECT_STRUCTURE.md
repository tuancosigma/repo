# Project Structure

Tài liệu mô tả cấu trúc thư mục và tổ chức code của project.

## Cấu trúc Thư mục

```
.
├── app.py                      # Flask web application (main entry point)
├── config.py                   # Configuration và settings
├── db_helpers.py               # Database helper functions
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (không commit vào git)
├── .gitignore                  # Git ignore rules
├── deploy.sh                   # Deployment script cho production
│
├── scripts/                     # CLI count scripts
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
├── tests/                      # Test và verification scripts
│   ├── test_ttl.py             # Test TTL index behavior
│   ├── verify_cache.py         # Verify cache contents
│   ├── verify_cache_raw.py     # Verify raw documents trong cache
│   ├── check_cache.py          # Check cache status
│   ├── compare_cache_vs_raw.py # So sánh cache vs source data
│   ├── search_cache.py         # Search từ cache collections
│   └── search_raw_data.py      # Search từ source collections
│
├── docs/                       # Documentation
│   ├── README.md              # Main documentation (đã move từ root)
│   ├── DEPLOYMENT.md          # Hướng dẫn deploy
│   ├── CRONJOB_SETUP.md       # Hướng dẫn setup cronjob
│   ├── CACHE_TTL_EXPLANATION.md # Giải thích TTL cache
│   ├── PROJECT_STRUCTURE.md   # File này
│   └── test_local.md          # Notes về local testing
│
├── templates/                  # HTML templates cho Flask
│   └── dashboard.html         # Main dashboard page
│
├── static/                     # Static files (CSS, JS, images)
│   ├── style.css              # Dashboard styles
│   ├── dashboard.js           # Dashboard JavaScript
│   └── logo.png               # Company logo
│
└── public/                     # Public assets
    └── logo.png               # Logo source file
```

## Mô tả các Thư mục

### Root Directory
- **app.py**: Flask web application, entry point chính cho dashboard
- **config.py**: Configuration và settings
- **db_helpers.py**: Database helper functions và aggregation pipelines
- **requirements.txt**: Danh sách Python packages cần thiết
- **.env**: Environment variables (không commit vào git)
- **deploy.sh**: Script tự động hóa deployment

### scripts/
Chứa các CLI scripts để đếm số lượng documents từ MongoDB:
- Daily scripts: Đếm trong 24 giờ gần nhất
- Weekly scripts: Đếm trong 7 ngày gần nhất
- Mỗi script độc lập, có thể chạy riêng lẻ

### tests/
Chứa các scripts để test và verify hệ thống:
- Test scripts: Kiểm tra behavior của các components
- Verify scripts: Xác minh data integrity
- Search scripts: Demo cách search từ cache và source collections

### docs/
Chứa tất cả documentation:
- **README.md**: Main documentation với quick start guide (ở root)
- **DEPLOYMENT.md**: Chi tiết về cách deploy lên production
- **PROJECT_STRUCTURE.md**: File này

### templates/
HTML templates cho Flask web application

### static/
Static files được serve bởi Flask:
- CSS styles
- JavaScript code
- Images (logo)

### public/
Public assets, source files cho logo

## Quy tắc Tổ chức

1. **Scripts được phân loại theo chức năng**:
   - Count scripts → `scripts/`
   - Setup/maintenance → `tools/`
   - Test/verify → `tests/`

2. **Documentation tập trung**:
   - Tất cả `.md` files → `docs/`
   - README.md ở root để dễ tìm

3. **Shared code**:
   - `config.py` và `db_helpers.py` ở root để dễ import
   - Các scripts import trực tiếp từ pymongo và dotenv

4. **Environment**:
   - `.env` không commit vào git
   - `.env.example` là template

## Import Paths

Khi import từ các scripts:

```python
# Từ scripts/ - import trực tiếp
from pymongo import MongoClient
from dotenv import load_dotenv
import os

# Hoặc từ root modules
from config import MONGO_URI
from db_helpers import get_collection
```

## Best Practices

1. **Luôn sử dụng relative paths** khi có thể
2. **Import từ root** cho shared utilities
3. **Giữ cấu trúc nhất quán** khi thêm files mới
4. **Document mọi thay đổi** trong docs/
