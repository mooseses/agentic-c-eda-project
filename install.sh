#!/bin/bash
set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==================================="
echo "  Agentic C-EDA Installation"
echo "==================================="
echo ""
echo "Install directory: $INSTALL_DIR"
echo ""

if [[ $EUID -ne 0 ]]; then
   echo "Error: This script must be run as root (use sudo)" 
   exit 1
fi

echo "[1/5] Creating virtual environment for daemon..."
cd daemon
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cd ..

echo "[2/5] Creating data directory..."
mkdir -p /var/lib/agentic-c-eda/logs
chmod 777 /var/lib/agentic-c-eda

echo "[3/5] Creating PTY socket directory..."
mkdir -p "$INSTALL_DIR/daemon/.agent"
chmod 755 "$INSTALL_DIR/daemon/.agent"

echo "[4/5] Installing systemd service..."
sed "s|INSTALL_DIR|$INSTALL_DIR|g" daemon/systemd/agent-daemon.service.template > /etc/systemd/system/C-EDA-daemon.service
systemctl daemon-reload

echo "[5/5] Enabling service..."
systemctl enable C-EDA-daemon.service

echo ""
echo "✓ Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Review configuration in config.py"
echo "  2. Start daemon: sudo systemctl start C-EDA-daemon"
echo "  3. Check status: sudo systemctl status C-EDA-daemon"
echo "  4. View logs: sudo journalctl -u C-EDA-daemon -f"
echo "  5. Start web dashboard: docker compose up -d"
echo ""
