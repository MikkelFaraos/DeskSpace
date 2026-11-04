#!/usr/bin/env bash
# Builds dist/deskspace_<version>_amd64.deb on Ubuntu or Debian (needs python3, pip and dpkg-deb).
#
# The package installs the app to /opt/deskspace, with its Python libraries bundled in
# /opt/deskspace/lib: Ubuntu's and Mint's own Qt for Python packages are too old or missing. The
# compiled libraries are built for Python's stable ABI, so one package runs on Python 3.10 and newer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)".*/\1/p' "$ROOT/fences/common.py")"
STAGE="$ROOT/dist/deb/deskspace_${VERSION}_amd64"
OUTPUT="$ROOT/dist/deskspace_${VERSION}_amd64.deb"

echo "== Files"
rm -rf "$STAGE" "$OUTPUT"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt/deskspace/app" "$STAGE/usr/bin" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/256x256/apps" "$STAGE/usr/share/doc/deskspace"
cp "$ROOT/deskspace.pyw" "$STAGE/opt/deskspace/app/"
cp -r "$ROOT/fences" "$STAGE/opt/deskspace/app/"
rm -rf "$STAGE/opt/deskspace/app/fences/__pycache__"

echo "== Python libraries"
python3 -m pip install --quiet --disable-pip-version-check --no-compile \
    --target "$STAGE/opt/deskspace/lib" -r "$ROOT/build/requirements-linux.txt"

cat > "$STAGE/usr/bin/deskspace" <<'EOF'
#!/bin/sh
export DESKSPACE_LAUNCHER=/usr/bin/deskspace
exec /usr/bin/python3 /opt/deskspace/app/deskspace.pyw "$@"
EOF
chmod 755 "$STAGE/usr/bin/deskspace"

cat > "$STAGE/usr/share/applications/deskspace.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=DeskSpace
Comment=Fences and widgets for your desktop
Exec=/usr/bin/deskspace
Icon=deskspace
Categories=Utility;
EOF

echo "== Icon"
QT_QPA_PLATFORM=offscreen PYTHONPATH="$STAGE/opt/deskspace/app:$STAGE/opt/deskspace/lib" python3 - \
    "$STAGE/usr/share/icons/hicolor/256x256/apps/deskspace.png" <<'EOF'
import sys
from PySide6.QtGui import QGuiApplication
app = QGuiApplication([])
from fences.common import make_app_icon
make_app_icon().pixmap(256, 256).save(sys.argv[1])
EOF

cat > "$STAGE/DEBIAN/control" <<EOF
Package: deskspace
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Maintainer: MikkelFaraos <MikkelFaraos@users.noreply.github.com>
Homepage: https://github.com/MikkelFaraos/DeskSpace
Installed-Size: $(du -sk "$STAGE" | cut -f1)
Depends: python3 (>= 3.10), xdg-utils, libglib2.0-bin, libegl1, libgl1, libdbus-1-3, libfontconfig1, libx11-xcb1,
 libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0,
 libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xinerama0,
 libxcb-xkb1
Recommends: playerctl, upower, network-manager, plocate, iputils-ping
Description: Fences, widgets and desktop pets for your desktop
 Translucent fences for desktop icons, plus sticky notes, a to-do list, a
 clock and calendar, system and network monitors, media controls, a package
 tracker, quick search, file tidy, Steam game tiles, a full-screen Console
 mode for a PC on a TV, and small animals that wander along your fences.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
EOF
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
update-desktop-database -q /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
EOF
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

printf 'DeskSpace %s\nhttps://github.com/MikkelFaraos/DeskSpace\n' "$VERSION" > "$STAGE/usr/share/doc/deskspace/README"

echo "== Package"
find "$STAGE" -type d -exec chmod 755 {} +
dpkg-deb --root-owner-group --build "$STAGE" "$OUTPUT"
du -h "$OUTPUT"
