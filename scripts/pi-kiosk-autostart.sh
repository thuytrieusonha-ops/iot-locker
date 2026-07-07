#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/home/raspi3/.local/state/smartlocker"
LOG_FILE="$LOG_DIR/kiosk.log"

mkdir -p "$LOG_DIR"

{
  printf '\n[%s] smartlocker kiosk autostart\n' "$(date --iso-8601=seconds)"
  export DISPLAY="${DISPLAY:-:0}"
  export XAUTHORITY="${XAUTHORITY:-/home/raspi3/.Xauthority}"
  cd /home/raspi3/Documents/iot-locker
  exec ./scripts/pi-kiosk.sh .env.docker http://127.0.0.1:8000
} >>"$LOG_FILE" 2>&1
