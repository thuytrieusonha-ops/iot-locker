#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/smartlocker-kiosk.desktop"

mkdir -p "$AUTOSTART_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Smart Locker Kiosk
Comment=Open the local smart locker web UI in Firefox kiosk mode
Exec=$PROJECT_DIR/scripts/pi-kiosk-autostart.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

chmod +x "$PROJECT_DIR/scripts/pi-kiosk.sh" "$PROJECT_DIR/scripts/pi-kiosk-autostart.sh"

echo "Da cai autostart: $DESKTOP_FILE"
echo "Lan sau khi Raspberry Pi dang nhap vao desktop, Firefox se mo kiosk local."
