#!/bin/bash

set -e

echo "=== FunPay Cardinal + API Delivery + Admin UI Install ==="

# Install Python dependencies
pip3 install -r requirements.txt --break-system-packages

# Create directories
mkdir -p storage/products logs configs

# Generate a UUID for the plugin (informational)
echo "Plugin UUID: 7f3b8e2a-9c1d-4b5e-a6f0-d2c3b4a5e6f7"
echo "(This is already set in plugins/api_delivery.py)"

# Start Cardinal with pm2
pm2 start "python3 main.py" --name funpay-cardinal --cwd "$PWD"
pm2 start "uvicorn admin.main:app --host 0.0.0.0 --port 7430" --name funpay-admin --cwd "$PWD"

# Save pm2 process list
pm2 save

echo ""
echo "✅ Installation complete!"
echo "   - Cardinal: running under pm2 (funpay-cardinal)"
echo "   - Admin UI: running under pm2 (funpay-admin) on port 7430"
echo "   - Admin URL: http://YOUR_VPS_IP:7430"
echo "   - Default admin login: admin / changeme"
echo ""
echo "⚠️  IMPORTANT: Update the API keys in the Admin UI at /settings"
echo "   and configure your lots at /lots before using the plugin."