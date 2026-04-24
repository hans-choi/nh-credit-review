#!/bin/bash
# NH 여신심사 AI 판독 POC — start script
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "Creating venv..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r backend/requirements.txt

mkdir -p backend/uploads backend/data
cd backend
echo ""
echo "==========================================="
echo " NH 여신심사 AI 판독 POC"
echo " http://localhost:8010"
echo "==========================================="
echo ""
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
