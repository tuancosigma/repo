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
echo -e "\n${GREEN}[1/2] Installing dependencies...${NC}"
pip3 install -q -r requirements.txt
echo -e "${GREEN}✓ Dependencies installed${NC}"

echo -e "\n${GREEN}=========================================="
echo "Deployment completed successfully!"
echo "==========================================${NC}"

echo -e "\n${YELLOW}Next steps:${NC}"
echo "Start dashboard:"
echo "   python3 app.py"
echo "   OR"
echo "   gunicorn -w 4 -b 0.0.0.0:5000 app:app"
echo ""
