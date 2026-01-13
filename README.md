# Agentic C-EDA Deployment Guide

[Benchmark Suite User Guide](./benchmarks/README.md)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/mooseses/agentic-c-eda.git
cd agentic-c-eda

# Run installer (as root)
sudo ./install.sh

# Start the daemon
sudo systemctl start C-EDA-daemon

# Start web dashboard
docker compose up -d

# Get your API key (displayed at startup)
docker compose logs web | grep "API Key"
```

Access dashboard at: `http://localhost:8000`

**Note:** The `-d` flag runs containers in detached mode (background). To see startup logs including the API key in real-time, run `docker compose up` without `-d`, or check logs afterwards with `docker compose logs web`.

## Installation Details

### Prerequisites
- **OS**: Linux (Ubuntu/Debian recommended)
- **Python**: 3.11+
- **Docker**: For web dashboard
- **Permissions**: Root access for daemon

### What the Installer Does

1. Creates Python virtual environment in `./venv`
2. Installs dependencies from `requirements.txt`
3. Creates `/var/lib/agentic-c-eda` for database
4. Sets up systemd service at `/etc/systemd/system/agent-daemon.service`
5. Enables service to start on boot

### Manual Installation

If you prefer manual setup:

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r daemon/requirements.txt

# 2. Create data directory
sudo mkdir -p /var/lib/agentic-c-eda/logs
sudo chmod 777 /var/lib/agentic-c-eda

# 3. Update service file
INSTALL_DIR=$(pwd)
sed "s|INSTALL_DIR|$INSTALL_DIR|g" daemon/systemd/agent-daemon.service.template > C-EDA-daemon.service

# 4. Install service
sudo cp C-EDA-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable C-EDA-daemon
sudo systemctl start C-EDA-daemon

# 5. Start web dashboard
docker compose up -d
```

## Architecture

**Project Structure:**
```
agentic-c-eda/
├── daemon/              # Host service (runs as root via systemd)
│   ├── main.py         # Entry point & event loop
│   ├── config.py       # Configuration & LLM settings
│   ├── watchdog.py     # System 1: Fast regex log parsing
│   ├── logic.py        # System 2: LLM batch analysis
│   ├── firewall.py     # iptables controller
│   ├── database.py     # SQLite operations
│   ├── service_discovery.py  # Port scanner with LLM
│   ├── pty_manager.py  # PTY session management
│   └── pty_service.py  # PTY IPC service
│
├── web-dashboard/      # Web UI (runs in Docker)
│   ├── api.py         # FastAPI REST endpoints
│   ├── agent.py       # Tool-use chat agent
│   ├── chat.py        # Chat engine
│   ├── models.py      # Pydantic schemas
│   ├── tools.py       # Agent toolkits
│   ├── auth.py        # API key authentication
│   ├── database.py    # Shared database access
│   └── static/        # Vue.js frontend
│
├── systemd/           # Service templates
├── docker-compose.yml # Web container config
├── Dockerfile         # Web container image
└── install.sh         # Automated installer
```

- **Host Daemon**: Runs as systemd service with root privileges
  - Monitors network via iptables
  - Analyzes events with LLM
  - Manages PTY sessions
  
- **Web Dashboard**: Runs in Docker container (unprivileged)
  - FastAPI REST API
  - Vue.js frontend
  - Real-time event streaming

## Configuration

### LLM Settings

**For Daemon (daemon/config.py):**
```python
LLM_API_URL = "http://localhost:1234/v1/chat/completions"  # LM Studio default
LLM_MODEL = "qwen/qwen3-4b-2507"
AGENT_SENSITIVITY = 5  # 1-10 scale
```

**For Web Dashboard (Docker):**

The web dashboard runs in Docker and needs to use `host.docker.internal` instead of `localhost` to reach services on your host machine.

In the web UI Settings page, set:
- **LLM API URL**: `http://host.docker.internal:1234/v1/chat/completions`
- **Model**: `qwen/qwen3-4b-2507`

Or update via database:
```bash
docker compose exec web python3 -c "
from database import get_db
db = get_db()
db.set_config('llm_api_url', 'http://host.docker.internal:1234/v1/chat/completions')
db.set_config('llm_model', 'qwen/qwen3-4b-2507')
"
```

## Service Management

```bash
# Check status
sudo systemctl status C-EDA-daemon

# View logs
sudo journalctl -u C-EDA-daemon -f

# Restart
sudo systemctl restart C-EDA-daemon

# Stop
sudo systemctl stop C-EDA-daemon

# Disable (prevent auto-start)
sudo systemctl disable C-EDA-daemon
```

## Uninstallation

```bash
# Stop and disable service
sudo systemctl stop C-EDA-daemon
sudo systemctl disable C-EDA-daemon

# Remove service file
sudo rm /etc/systemd/system/C-EDA-daemon.service
sudo systemctl daemon-reload

# Stop web dashboard
docker compose down

# Optional: Remove data
sudo rm -rf /var/lib/agentic-c-eda
```

## Troubleshooting

**Service fails to start:**
```bash
# Check logs
sudo journalctl -u C-EDA-daemon -n 50

# Verify Python path
which python3

# Check permissions
ls -la /var/lib/agentic-c-eda
```

**Web dashboard can't connect:**
- Ensure daemon is running: `sudo systemctl status C-EDA-daemon`
- Check database exists: `ls -la /var/lib/agentic-c-eda/`
- Verify PTY socket: `ls -la daemon/.agent/pty.sock`

**iptables rules not working:**
- Service must run as root
- Check kernel modules: `sudo modprobe ip_tables`

## Development Mode

For testing without systemd:

```bash
# 1. Setup Python environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r daemon/requirements.txt
pip install -r web-dashboard/requirements.txt

# 3. Terminal 1: Run daemon
sudo ./venv/bin/python daemon/main.py
```

In another terminal:
```bash
# Terminal 2: Run web dashboard
cd web-dashboard

# Set PTY socket path for dev mode (points to daemon's socket)
export AGENT_PTY_SOCKET=/home/pete/Downloads/agentic-ips-daemon/daemon/.agent/pty.sock

# Or run inline:
AGENT_PTY_SOCKET=$(realpath ../daemon/.agent/pty.sock) ../venv/bin/uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

**Note**: In production Docker mode, the socket is mounted at `/app/.agent/pty.sock` via docker-compose volumes.
