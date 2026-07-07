#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.docker}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Khong tim thay file env: $ENV_FILE"
  echo "Hay tao bang lenh: cp .env.docker.example .env.docker"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

KIOSK_URL="${2:-${SMARTLOCKER_KIOSK_URL:-http://127.0.0.1:8000}}"
KIOSK_BROWSER="${SMARTLOCKER_KIOSK_BROWSER:-firefox}"
KIOSK_PROFILE_DIR="${SMARTLOCKER_KIOSK_PROFILE_DIR:-/tmp/smartlocker-kiosk-browser}"
KIOSK_ZOOM="${SMARTLOCKER_KIOSK_ZOOM:-1.0}"
KIOSK_FIREFOX_AUTO_ALLOW_CAMERA="${SMARTLOCKER_KIOSK_FIREFOX_AUTO_ALLOW_CAMERA:-false}"
KIOSK_FIREFOX_DIRECT="${SMARTLOCKER_KIOSK_FIREFOX_DIRECT:-false}"

if [[ ! "$KIOSK_ZOOM" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "SMARTLOCKER_KIOSK_ZOOM khong hop le: $KIOSK_ZOOM"
  echo "Vi du hop le: 1.0, 1.15, 1.25"
  exit 1
fi

append_kiosk_query() {
  local url="$1"
  if [[ "$url" == *"_kiosk"* ]]; then
    printf '%s\n' "$url"
  elif [[ "$url" == *"?"* ]]; then
    printf '%s\n' "${url}&_kiosk=1"
  else
    printf '%s\n' "${url}?_kiosk=1"
  fi
}

KIOSK_URL="$(append_kiosk_query "$KIOSK_URL")"

web_ready() {
  curl --fail --silent --show-error "$KIOSK_URL" >/dev/null
}

start_app() {
  if docker container inspect smartlocker-app smartlocker-mysql smartlocker-mqtt >/dev/null 2>&1; then
    echo "Khoi dong nhanh container smartlocker da co..."
    if docker start smartlocker-mysql smartlocker-mqtt smartlocker-app >/dev/null; then
      return 0
    fi
    echo "Khoi dong nhanh khong thanh cong, chuyen sang docker compose..."
  fi

  echo "Dang khoi dong ung dung bang image/container local..."
  if docker compose --env-file "$ENV_FILE" up -d app; then
    return 0
  fi

  echo "Khoi dong local khong thanh cong, thu build app..."
  docker compose --env-file "$ENV_FILE" up -d --build app
}

if web_ready; then
  echo "Ung dung da san sang tai: $KIOSK_URL"
else
  echo "Ung dung chua san sang tai: $KIOSK_URL"
  start_app
fi

for _ in $(seq 1 60); do
  if web_ready; then
    break
  fi
  sleep 1
done

if ! web_ready; then
  echo "Ung dung chua san sang tai: $KIOSK_URL"
  exit 1
fi

mkdir -p "$KIOSK_PROFILE_DIR/chromium" "$KIOSK_PROFILE_DIR/firefox"

if [[ "$KIOSK_BROWSER" == "auto" || "$KIOSK_BROWSER" == "chromium" ]]; then
  if command -v chromium-browser >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium-browser"
  elif command -v chromium >/dev/null 2>&1; then
    CHROMIUM_BIN="chromium"
  elif command -v google-chrome >/dev/null 2>&1; then
    CHROMIUM_BIN="google-chrome"
  else
    CHROMIUM_BIN=""
  fi

  if [[ -n "$CHROMIUM_BIN" ]]; then
    exec "$CHROMIUM_BIN" \
      --kiosk "$KIOSK_URL" \
      --user-data-dir="$KIOSK_PROFILE_DIR/chromium" \
      --high-dpi-support=1 \
      --force-device-scale-factor="$KIOSK_ZOOM" \
      --no-first-run \
      --no-default-browser-check \
      --noerrdialogs \
      --disable-background-networking \
      --disable-component-update \
      --disable-default-apps \
      --disable-session-crashed-bubble \
      --disable-sync \
      --disable-translate \
      --disable-infobars \
      --disable-features=Translate,BackForwardCache,MediaRouter \
      --disable-dev-shm-usage \
      --disk-cache-size=52428800 \
      --hide-scrollbars \
      --overscroll-history-navigation=0 \
      --check-for-update-interval=31536000
  fi
fi

if command -v firefox-esr >/dev/null 2>&1; then
  FIREFOX_BIN="firefox-esr"
elif command -v firefox >/dev/null 2>&1; then
  FIREFOX_BIN="firefox"
else
  echo "Khong tim thay trinh duyet kiosk. Hay cai chromium-browser hoac firefox-esr."
  exit 1
fi

if [[ "$KIOSK_FIREFOX_DIRECT" == "true" || "$KIOSK_FIREFOX_DIRECT" == "1" ]]; then
  echo "Mo Firefox kiosk truc tiep: $KIOSK_URL"
  exec "$FIREFOX_BIN" -kiosk "$KIOSK_URL"
fi

if pgrep -f "$FIREFOX_BIN.*$KIOSK_PROFILE_DIR/firefox" >/dev/null 2>&1; then
  echo "Dong tien trinh Firefox kiosk cu..."
  pkill -f "$FIREFOX_BIN.*$KIOSK_PROFILE_DIR/firefox" || true
  sleep 2
fi

rm -f \
  "$KIOSK_PROFILE_DIR/firefox/lock" \
  "$KIOSK_PROFILE_DIR/firefox/.parentlock" \
  "$KIOSK_PROFILE_DIR/firefox/parent.lock"

cat > "$KIOSK_PROFILE_DIR/firefox/user.js" <<'EOF'
user_pref("browser.cache.disk.capacity", 51200);
user_pref("browser.cache.disk.smart_size.enabled", false);
user_pref("browser.link.open_newwindow", 1);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("dom.disable_beforeunload", true);
user_pref("general.smoothScroll", false);
user_pref("media.hardware-video-decoding.enabled", true);
user_pref("toolkit.telemetry.enabled", false);
user_pref("toolkit.telemetry.unified", false);
EOF
printf 'user_pref("layout.css.devPixelsPerPx", "%s");\n' "$KIOSK_ZOOM" >> "$KIOSK_PROFILE_DIR/firefox/user.js"

if [[ "$KIOSK_FIREFOX_AUTO_ALLOW_CAMERA" == "true" || "$KIOSK_FIREFOX_AUTO_ALLOW_CAMERA" == "1" ]]; then
  printf '%s\n' 'user_pref("media.navigator.permission.disabled", true);' >> "$KIOSK_PROFILE_DIR/firefox/user.js"
fi

exec "$FIREFOX_BIN" --kiosk --profile "$KIOSK_PROFILE_DIR/firefox" "$KIOSK_URL"
