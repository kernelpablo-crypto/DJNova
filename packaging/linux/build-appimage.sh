#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> Compilando bundle DJNova con PyInstaller..."
pyinstaller --noconfirm --clean packaging/linux/djnova-linux.spec

APPDIR="$ROOT_DIR/build/DJNova.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

cp -a dist/DJNova/* "$APPDIR/usr/bin/"
cp assets/logo/djnova-256.png "$APPDIR/djnova.png"
cp assets/logo/djnova-256.png "$APPDIR/.DirIcon"
cp assets/logo/djnova-256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/djnova.png"
sed 's|@EXEC@|DJNova|g; s|@ICON@|djnova|g' packaging/linux/djnova.desktop > "$APPDIR/djnova.desktop"
cp "$APPDIR/djnova.desktop" "$APPDIR/usr/share/applications/djnova.desktop"

# Incluir plugins GStreamer del sistema si existen para portabilidad entre distros
GST_SYS_DIR="$(pkg-config --variable=pluginsdir gstreamer-1.0 2>/dev/null || echo /usr/lib/x86_64-linux-gnu/gstreamer-1.0)"
if [ -d "$GST_SYS_DIR" ]; then
  mkdir -p "$APPDIR/usr/lib/gstreamer-1.0"
  cp -a "$GST_SYS_DIR"/*.so "$APPDIR/usr/lib/gstreamer-1.0/" 2>/dev/null || true
fi

cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
if [ -d "$HERE/usr/lib/gstreamer-1.0" ]; then
  export GST_PLUGIN_PATH="$HERE/usr/lib/gstreamer-1.0${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"
fi
exec "$HERE/usr/bin/DJNova" "$@"
EOF
chmod +x "$APPDIR/AppRun"

TOOL="$ROOT_DIR/build/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$TOOL"
fi

mkdir -p "$ROOT_DIR/dist"
export ARCH=x86_64
export APPIMAGE_EXTRACT_AND_RUN=1
"$TOOL" "$APPDIR" "$ROOT_DIR/dist/DJNova-x86_64.AppImage"
chmod +x "$ROOT_DIR/dist/DJNova-x86_64.AppImage"

echo "✅ AppImage generado: $ROOT_DIR/dist/DJNova-x86_64.AppImage"
