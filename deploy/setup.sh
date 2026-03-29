#!/usr/bin/env bash
# SuzyCloud — Server setup script (idempotent)
set -euo pipefail

APP_DIR="/home/suzy/SuzyCloud"
ENV_DIR="$HOME/.suzycloud"
SERVICE_NAME="suzycloud"

echo "=== SuzyCloud Setup ==="

# 1. Create virtualenv and install dependencies
if [ ! -d "$APP_DIR/venv" ]; then
    echo "Creating virtualenv..."
    python3 -m venv "$APP_DIR/venv"
else
    echo "Virtualenv already exists."
fi

echo "Installing dependencies..."
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "Dependencies installed."

# 2. Create required directories
for dir in personas data media; do
    mkdir -p "$APP_DIR/$dir"
done
echo "Directories ensured: personas/, data/, media/"

# 3. Create ~/.suzycloud/.env template (only if missing)
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_DIR/.env" ]; then
    cat > "$ENV_DIR/.env" << 'ENVEOF'
# SuzyCloud — Global secrets
# Shared infrastructure credentials (per-persona secrets go in personas/{key}/.env)

# Waha WhatsApp API
WAHA_API_URL=http://localhost:3000
WAHA_API_KEY=
WAHA_SESSION=default

# Webhook
WEBHOOK_SECRET=

# Dashboard
DASHBOARD_SECRET=

# Gateway (Baileys)
GATEWAY_API_KEY=
ENVEOF
    echo "Created $ENV_DIR/.env template — fill in your secrets."
else
    echo "$ENV_DIR/.env already exists, skipping."
fi

# 4. Install systemd service
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
echo "Installing systemd service..."
sudo cp "$APP_DIR/deploy/${SERVICE_NAME}.service" "$SERVICE_FILE"
sudo systemctl daemon-reload
echo "Service file installed."

# 5. Enable and start service
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Service already running — restarting..."
    sudo systemctl restart "$SERVICE_NAME"
else
    echo "Enabling and starting service..."
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"
fi

echo ""
echo "=== Setup complete ==="
echo "Service status:"
systemctl status "$SERVICE_NAME" --no-pager -l || true
echo ""
echo "Next steps:"
echo "  1. Fill in secrets: $ENV_DIR/.env"
echo "  2. Add Caddy snippet from deploy/caddy-snippet.txt"
echo "  3. Create your first persona directory under personas/"
