#!/usr/bin/env bash
# Installs DeskSpace for the current user on Ubuntu, Linux Mint and other apt-based systems:
#
#     bash build/install-linux.sh
#
# The app goes in ~/.local/share/deskspace and needs no root, apart from the system packages below.
# Your fences and settings live in ~/DeskSpace and are left alone by installing or uninstalling.
# To remove it again:  bash build/install-linux.sh --uninstall
set -euo pipefail

APP_DIR="$HOME/.local/share/deskspace"
BIN="$HOME/.local/bin/deskspace"
LAUNCHER="$HOME/.local/share/applications/deskspace.desktop"
ICON="$HOME/.local/share/icons/hicolor/256x256/apps/deskspace.png"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/deskspace.desktop"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
    pkill -f "$APP_DIR/app/deskspace.pyw" || true
    rm -rf "$APP_DIR"
    rm -f "$BIN" "$LAUNCHER" "$ICON" "$AUTOSTART"
    echo "DeskSpace is removed. Your fences and settings are still in ~/DeskSpace."
    exit 0
fi

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "DeskSpace needs Python 3.10 or newer." >&2
    exit 1
fi

if command -v apt-get > /dev/null; then
    echo "== System packages (this asks for your password)"
    # python3-venv and the xcb libraries Qt needs; the rest are small tools the widgets read from.
    sudo apt-get install -y python3-venv libxcb-cursor0 libxkbcommon-x11-0 libegl1 \
        playerctl upower network-manager plocate iputils-ping
fi

echo "== Python libraries"
mkdir -p "$APP_DIR"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check -r "$SOURCE/build/requirements-linux.txt"

echo "== App"
pkill -f "$APP_DIR/app/deskspace.pyw" || true
rm -rf "$APP_DIR/app"
mkdir -p "$APP_DIR/app"
cp "$SOURCE/deskspace.pyw" "$APP_DIR/app/"
cp -r "$SOURCE/fences" "$APP_DIR/app/"
rm -rf "$APP_DIR/app/fences/__pycache__"

mkdir -p "$(dirname "$BIN")" "$(dirname "$LAUNCHER")" "$(dirname "$ICON")"
cat > "$BIN" <<EOF
#!/bin/sh
export DESKSPACE_LAUNCHER="$BIN"
exec "$APP_DIR/venv/bin/python" "$APP_DIR/app/deskspace.pyw" "\$@"
EOF
chmod +x "$BIN"

QT_QPA_PLATFORM=offscreen PYTHONPATH="$APP_DIR/app" "$APP_DIR/venv/bin/python" - "$ICON" <<'EOF'
import sys
from PySide6.QtGui import QGuiApplication
app = QGuiApplication([])
from fences.common import make_app_icon
make_app_icon().pixmap(256, 256).save(sys.argv[1])
EOF

cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=DeskSpace
Comment=Fences and widgets for your desktop
Exec=$BIN
Icon=deskspace
Categories=Utility;
EOF

echo "== Done: starting DeskSpace"
nohup "$BIN" > /dev/null 2>&1 &
echo "DeskSpace is running. It starts by itself when you sign in; change that in its Settings."
