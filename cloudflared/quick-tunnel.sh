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

wait_for_origin() {
    local name="$1"
    local target="$2"
    local max_attempts="${SMARTLOCKER_ORIGIN_CHECK_ATTEMPTS:-20}"
    local sleep_seconds="${SMARTLOCKER_ORIGIN_CHECK_SLEEP_SECONDS:-1}"
    local attempt=0

    while (( attempt < max_attempts )); do
        if python3 - "${target}" <<'PY' >/dev/null 2>&1
import sys
from urllib.parse import urlparse
import http.client

target = sys.argv[1]
parts = urlparse(target)
port = parts.port or (443 if parts.scheme == "https" else 80)
path = parts.path or "/"
if parts.query:
    path += "?" + parts.query

conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
conn = conn_cls(parts.hostname, port, timeout=3)
try:
    conn.request("GET", path)
    response = conn.getresponse()
    if 100 <= response.status < 600:
        sys.exit(0)
finally:
    conn.close()
PY
        then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep "${sleep_seconds}"
    done

    echo "Origin ${name} chua san sang tai ${target}. Hay start app local truoc khi mo Quick Tunnel." >&2
    return 1
}

cleanup() {
    for pid in "${APP_PID:-}" "${MONITOR_PID:-}"; do
        if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
            kill "${pid}" >/dev/null 2>&1 || true
        fi
    done
}

trap cleanup EXIT INT TERM

rm -f "${APP_LOG}" "${MONITOR_LOG}"

start_quick_tunnel() {
    local name="$1"
    local target="$2"
    local logfile="$3"
    local max_attempts="${SMARTLOCKER_QUICK_TUNNEL_MAX_ATTEMPTS:-6}"
    local attempt=0
    local wait_seconds="${SMARTLOCKER_QUICK_TUNNEL_WAIT_SECONDS:-45}"

    while (( attempt < max_attempts )); do
        attempt=$((attempt + 1))
        echo "Dang mo Quick Tunnel cho ${name} tai ${target} (attempt ${attempt}/${max_attempts})" >&2
        rm -f "${logfile}"
        cloudflared tunnel --url "${target}" >"${logfile}" 2>&1 &
        local pid=$!

        # Wait for URL to appear in log. Quick Tunnel can be slow or temporarily fail server-side.
        local waited=0
        while (( waited < wait_seconds )); do
            if grep -qE 'https://[-a-z0-9]+\.trycloudflare\.com' "${logfile}" 2>/dev/null; then
                local url
                url=$(grep -oE 'https://[-a-z0-9]+\.trycloudflare\.com' "${logfile}" | head -n1)
                echo "${url}"
                if [[ "${name}" == "app" ]]; then
                    APP_PID=${pid}
                else
                    MONITOR_PID=${pid}
                fi
                return 0
            fi
            if grep -q '500 Internal Server Error' "${logfile}" 2>/dev/null; then
                echo "Cloudflare Quick Tunnel tam thoi loi 500 cho ${name}; se thu lai..." >&2
                break
            fi
            if grep -q 'failed to unmarshal quick Tunnel' "${logfile}" 2>/dev/null; then
                echo "Cloudflared khong doc duoc response Quick Tunnel cho ${name}; se thu lai..." >&2
                break
            fi
            sleep 1
            waited=$((waited + 1))
        done

        echo "Attempt ${attempt} failed to obtain Quick Tunnel URL for ${name}. See ${logfile}. Killing pid ${pid} and retrying..." >&2
        kill "${pid}" >/dev/null 2>&1 || true
        sleep 5
    done

    echo "Khong lay duoc URL Quick Tunnel cho ${name} sau ${max_attempts} lan thu. Xem log: ${logfile}" >&2
    echo "Neu log co '500 Internal Server Error' thi day thuong la loi tam thoi phia Cloudflare Quick Tunnel, khong phai loi code cua ban." >&2
    return 1
}

wait_for_origin "app" "${APP_TARGET}"
wait_for_origin "monitor" "${MONITOR_TARGET}"

APP_URL="$(start_quick_tunnel "app" "${APP_TARGET}" "${APP_LOG}")"
if [[ $? -ne 0 ]]; then
    exit 1
fi
MONITOR_URL="$(start_quick_tunnel "monitor" "${MONITOR_TARGET}" "${MONITOR_LOG}")"
if [[ $? -ne 0 ]]; then
    exit 1
fi


if [[ -f "${ENV_FILE}" ]]; then
    python3 - "${ENV_FILE}" "${APP_URL}" "${MONITOR_URL}" <<'PY'
from pathlib import Path
import re
import sys

env_path = Path(sys.argv[1])
app_url = sys.argv[2]
monitor_url = sys.argv[3]
text = env_path.read_text(encoding="utf-8")

NOISE_PATTERNS = [
    re.compile(r"^Dang mo Quick Tunnel cho .+$"),
    re.compile(r"^https://[-a-z0-9]+\.trycloudflare\.com'?$"),
]


def sanitize_env_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if any(pattern.match(stripped) for pattern in NOISE_PATTERNS):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).rstrip("\n")

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

text = sanitize_env_text(text)
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
