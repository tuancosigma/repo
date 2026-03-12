#!/bin/bash

# Deployment script for production server
# Usage: ./deploy.sh

set -e

echo "=========================================="
echo "MongoDB Dashboard Deployment Script"
echo "=========================================="

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}Warning: .env file not found${NC}"
    echo "Copying .env.example to .env..."
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env file with your MongoDB credentials${NC}"
    exit 1
fi

# Install dependencies
echo -e "\n${GREEN}[1/5] Installing dependencies...${NC}"
pip3 install -q -r requirements.txt
echo -e "${GREEN}✓ Dependencies installed${NC}"

# Setup cache collection
echo -e "\n${GREEN}[2/5] Setting up cache collection...${NC}"
python3 tools/setup_cache.py
echo -e "${GREEN}✓ Cache collection setup completed${NC}"

# Setup indexes (optional but recommended)
echo -e "\n${GREEN}[3/5] Setting up database indexes...${NC}"
if [ -f tools/setup_indexes.py ]; then
    python3 tools/setup_indexes.py
    echo -e "${GREEN}✓ Indexes created${NC}"
else
    echo -e "${YELLOW}⚠ tools/setup_indexes.py not found, skipping${NC}"
fi

# Test cache ingestion
echo -e "\n${GREEN}[4/5] Testing cache ingestion...${NC}"
python3 tools/cache_ingest.py
echo -e "${GREEN}✓ Cache ingestion test successful${NC}"

# Verify cache
echo -e "\n${GREEN}[5/5] Verifying cache...${NC}"
python3 -c "
from pymongo import MongoClient
from dotenv import load_dotenv
import os
load_dotenv()
client = MongoClient(os.getenv('MONGODB_URL'))
cache = client['cache']['stats']
count = cache.count_documents({})
print(f'Cache documents: {count}')
if count > 0:
    print('✓ Cache is working!')
else:
    print('⚠ No cache documents found')
"

echo -e "\n${GREEN}=========================================="
echo "Deployment completed successfully!"
echo "==========================================${NC}"

echo -e "\n${YELLOW}Next steps:${NC}"
echo "1. Setup cronjob: crontab -e"
echo "   Add: */5 * * * * cd $(pwd) && python3 tools/cache_ingest.py >> /var/log/cache_ingest.log 2>&1"
echo ""
echo "2. Start dashboard:"
echo "   python3 app.py"
echo "   OR"
echo "   gunicorn -w 4 -b 0.0.0.0:5000 app:app"
echo ""
