#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Detect Wi-Fi / LAN IP
LAN_IP=$(python3 -c "import network_intel; print(network_intel.get_lan_ip())" 2>/dev/null || echo "127.0.0.1")
PORT=${PORT:-8000}

# Clean up previous instances and release port before starting
fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
sleep 0.2

# Cleanup function when user exits (Ctrl+C)
cleanup() {
    echo ""
    echo "[*] Shutting down AnonDrop & Public Tunnel..."
    python3 tunnel.py stop >/dev/null 2>&1 || true
    if [ -n "$UVICORN_PID" ]; then
        kill -TERM "$UVICORN_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start or verify the public tunnel is active
echo "[*] Starting public tunnel (so users outside your Wi-Fi can connect)..."
python3 tunnel.py start >/dev/null 2>&1 || true
PUBLIC_URL=$(python3 tunnel.py url 2>/dev/null || echo "")


echo "=================================================================="
echo "          ⚡ AnonDrop - Gen Z Anonymous Chat Server               "
echo "=================================================================="
echo "  💻 Host Laptop (You):        http://localhost:${PORT}/"
echo "  📱 Wi-Fi / Friends:          http://${LAN_IP}:${PORT}/"
if [ -n "$PUBLIC_URL" ]; then
echo "  🌐 Outside Wi-Fi (Anywhere): ${PUBLIC_URL}"
else
echo "  🌐 Outside Wi-Fi:            Run './tunnel.sh start' to activate"
fi
echo "  👑 Host Mode:                Automatic on Laptop (Zero Login)"
echo "=================================================================="
echo "  - 100% Anonymous for friends (custom avatars, handles, no login)"
echo "  - Share the Worldwide link with anyone outside Wi-Fi or on 4G/5G"
echo "  - Host Intel active on your laptop (Real Names & Device Info)"
echo "=================================================================="
echo ""

# Raise file descriptor limit so 1000+ concurrent WebSockets never run out of sockets
ulimit -n 65535 2>/dev/null || ulimit -n 4096 2>/dev/null || true

# Run uvicorn server optimized for 1000+ concurrent users:
# - No reload (prevents file watchers restarting on uploads/DB writes)
# - High backlog (4096) and concurrency limits (2500)
# - Strip uvicorn server header to prevent fingerprinting
python3 -m uvicorn app:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --backlog 4096 \
    --limit-concurrency 2500 \
    --no-server-header \
    --proxy-headers \
    --forwarded-allow-ips="127.0.0.1,::1" &
UVICORN_PID=$!

wait $UVICORN_PID

