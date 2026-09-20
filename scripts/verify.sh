#!/usr/bin/env bash
# Health gate for locadev — run on the Docker host (macOS or Linux).
# Probes only ports this stack publishes on 127.0.0.1 (no network scans).

set -u

HOST=127.0.0.1
FAIL=0

# Portable TCP check: nc if available, else python3 socket (macOS default bash is old).
port_open() {
  local port=$1
  if command -v nc >/dev/null 2>&1; then
    # -G is connect timeout on macOS; -w on Linux. Try both patterns.
    if nc -z -G 2 "$HOST" "$port" 2>/dev/null || nc -z -w 2 "$HOST" "$port" 2>/dev/null; then
      return 0
    fi
    return 1
  fi
  python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('$HOST', $port))
    sys.exit(0)
except Exception:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null
}

check() {
  local name=$1 port=$2 required=$3
  if port_open "$port"; then
    printf '[OK] %s :%s\n' "$name" "$port"
  else
    if [[ "$required" == "1" ]]; then
      printf '[FAIL] %s :%s\n' "$name" "$port"
      FAIL=1
    else
      printf '[--] %s :%s (profile off or not ready)\n' "$name" "$port"
    fi
  fi
}

echo "locadev verify (host $HOST)"
echo "--- core ---"
check "Azurite blob" 10000 1
check "Service Bus AMQP" 5672 1
check "Bridge" 8090 1
check "PGlite HTTP" 5433 1
check "Topaz REST" 8484 1
check "Redis" 6380 1
check "Service Bus HTTP" 5300 1

echo "--- optional profiles ---"
check "Cosmos" 8081 0
check "MiniStack AWS" 4566 0
check "Key Vault" 8443 0
check "AI Search" 8800 0
check "Mail (SendGrid)" 8095 0
check "fake-slack" 8096 0
check "fake-discord" 8097 0
check "Azure Functions" 7071 0
check "fake-teams" 3979 0
check "sample_service" 18080 0
check "Cloudflare Worker" 8787 0
check "fake-oauth" 8098 0
check "gigchain RPC" 26657 0
check "gigchain REST" 1317 0

if [[ "$FAIL" -ne 0 ]]; then
  echo "Core services FAILED."
  exit 1
fi
echo "Core services OK."
exit 0
