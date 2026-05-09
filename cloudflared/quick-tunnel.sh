#!/usr/bin/env bash
set -euo pipefail

APP_HOST="${SMARTLOCKER_APP_HOST:-127.0.0.1}"
APP_PORT="${SMARTLOCKER_APP_PORT:-8000}"
MONITOR_HOST="${SMARTLOCKER_MONITOR_HOST:-127.0.0.1}"
MONITOR_PORT="${SMARTLOCKER_MONITOR_PORT:-8001}"

APP_TARGET="http://${APP_HOST}:${APP_PORT}"
MONITOR_TARGET="http://${MONITOR_HOST}:${MONITOR_PORT}"
APP_LOG="/tmp/smartlocker-app-quick-tunnel.log"
MONITOR_LOG="/tmp/smartlocker-monitor-quick-tunnel.log"
ENV_FILE="${SMARTLOCKER_ENV_FILE:-.env}"

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "Khong tim thay lenh 'cloudflared'. Cai cloudflared truoc khi mo Quick Tunnel."
    exit 1
fi

cleanup() {
    for pid in "${APP_PID:-}" "${MONITOR_PID:-}"; do
        if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
            kill "${pid}" >/dev/null 2>&1 || true
        fi
    done
}

trap cleanup EXIT INT TERM

rm -f "${APP_LOG}" "${MONITOR_LOG}"

echo "Dang mo Quick Tunnel cho app tai ${APP_TARGET}"
cloudflared tunnel --url "${APP_TARGET}" >"${APP_LOG}" 2>&1 &
APP_PID=$!

echo "Dang mo Quick Tunnel cho monitor tai ${MONITOR_TARGET}"
cloudflared tunnel --url "${MONITOR_TARGET}" >"${MONITOR_LOG}" 2>&1 &
MONITOR_PID=$!

wait_for_url() {
    local logfile="$1"
    local name="$2"
    local waited=0

    while (( waited < 30 )); do
        if grep -qE 'https://[-a-z0-9]+\.trycloudflare\.com' "${logfile}" 2>/dev/null; then
            grep -oE 'https://[-a-z0-9]+\.trycloudflare\.com' "${logfile}" | head -n1
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    echo "Khong lay duoc URL Quick Tunnel cho ${name}. Xem log: ${logfile}" >&2
    return 1
}

APP_URL="$(wait_for_url "${APP_LOG}" "app")"
MONITOR_URL="$(wait_for_url "${MONITOR_LOG}" "monitor")"

if [[ -f "${ENV_FILE}" ]]; then
    python3 - "${ENV_FILE}" "${APP_URL}" "${MONITOR_URL}" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
app_url = sys.argv[2]
monitor_url = sys.argv[3]
text = env_path.read_text(encoding="utf-8")

def replace_or_append(text: str, key: str, value: str) -> str:
    line = f"{key}='{value}'"
    prefix = f"{key}="
    lines = text.splitlines()
    for idx, existing in enumerate(lines):
        if existing.startswith(prefix):
            lines[idx] = line
            break
    else:
        lines.append(line)
    return "\n".join(lines) + "\n"

text = replace_or_append(text, "SMARTLOCKER_BASE_URL", app_url)
text = replace_or_append(text, "SMARTLOCKER_MONITOR_URL", monitor_url)
env_path.write_text(text, encoding="utf-8")
PY
fi

cat <<EOF

Quick Tunnel da san sang:
- App: ${APP_URL}
- Monitor: ${MONITOR_URL}

Da cap nhat ${ENV_FILE}:
SMARTLOCKER_BASE_URL='${APP_URL}'
SMARTLOCKER_MONITOR_URL='${MONITOR_URL}'

Sau do restart:
uv run python monitor.py
uv run python kiosk.py

Luu y:
- Mail cu da gui truoc do se van dung link cu va co the hong.
- Hay tao mail moi sau khi restart de link mo tu dung URL Quick Tunnel moi.

Giu cua so nay mo de tunnel tiep tuc chay. Nhan Ctrl+C de tat ca hai tunnel.
EOF

wait
