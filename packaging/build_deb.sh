#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
version="$(python -c 'from duet import __version__; print(__version__)')"
package="dist/deb-root"
rm -rf "$package"
mkdir -p "$package/DEBIAN" "$package/opt/duet" "$package/usr/bin" "$package/usr/share/applications" "$package/usr/share/doc/duet"
cp -a dist/duet-desktop/. "$package/opt/duet/"
install -m 0644 LICENSE "$package/usr/share/doc/duet/copyright"
install -m 0644 packaging/THIRD_PARTY_NOTICES.txt "$package/usr/share/doc/duet/THIRD_PARTY_NOTICES.txt"
cat > "$package/DEBIAN/control" <<EOF
Package: duet
Version: $version
Section: net
Priority: optional
Architecture: amd64
Depends: libc6 (>= 2.39), libstdc++6, libgcc-s1, libgl1, libegl1, libfontconfig1, libdbus-1-3, libx11-xcb1, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-randr0, libxcb-shape0, libxcb-shm0, libxcb-xfixes0, libxcb-xinerama0, libxcb-xkb1
Maintainer: Duet maintainers
Description: Local file and text sharing between a phone and PC
 Duet runs a local sharing server and provides a Qt desktop interface.
EOF
cat > "$package/usr/bin/duet-desktop" <<'EOF'
#!/bin/sh
exec /opt/duet/duet-desktop "$@"
EOF
chmod 0755 "$package/usr/bin/duet-desktop"
cat > "$package/usr/share/applications/duet.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Duet
Comment=Share files and text with your phone
Exec=duet-desktop
Icon=utilities-terminal
Terminal=false
Categories=Network;FileTransfer;
EOF
chmod 0755 "$package/DEBIAN"
dpkg-deb --root-owner-group --build "$package" "dist/duet_${version}_amd64.deb"
