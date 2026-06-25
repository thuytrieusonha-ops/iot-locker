#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.docker}"
KIOSK_URL="${2:-http://127.0.0.1:8000}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Khong tim thay file env: $ENV_FILE"
  echo "Hay tao bang lenh: cp .env.docker.example .env.docker"
  exit 1
fi

docker compose --env-file "$ENV_FILE" up -d --build app

for _ in $(seq 1 60); do
  if curl --fail --silent --show-error "$KIOSK_URL" >/dev/null; then
    break
  fi
  sleep 1
done

if ! curl --fail --silent --show-error "$KIOSK_URL" >/dev/null; then
  echo "Ung dung chua san sang tai: $KIOSK_URL"
  exit 1
fi

if command -v firefox-esr >/dev/null 2>&1; then
  FIREFOX_BIN="firefox-esr"
elif command -v firefox >/dev/null 2>&1; then
  FIREFOX_BIN="firefox"
else
  echo "Khong tim thay Firefox. Hay cai firefox-esr hoac firefox."
  exit 1
fi

exec "$FIREFOX_BIN" --kiosk "$KIOSK_URL"
