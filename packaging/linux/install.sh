#!/usr/bin/env bash
set -euo pipefail

# Instalador universal de DJNova para Linux (Arch, Ubuntu/Debian, Fedora)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/djnova-app"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

echo "==> Instalando dependencias del sistema..."
if command -v pacman >/dev/null 2>&1; then
  sudo pacman -S --needed --noconfirm \
    python python-pyqt5 python-gobject gstreamer gst-plugins-base \
    gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav \
    python-appdirs python-sortedcontainers python-falcon python-mido \
    python-rtmidi python-numpy python-pillow python-pip \
    ffmpeg mpv yt-dlp qrencode qpwgraph
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y \
    python3 python3-venv python3-pip python3-pyqt5 python3-gi \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-pulseaudio \
    ffmpeg mpv yt-dlp qrencode python3-numpy python3-pil python3-appdirs \
    python3-sortedcontainers python3-falcon python3-mido python3-rtmidi
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y \
    python3 python3-qt5 python3-gobject gstreamer1 gstreamer1-plugins-base \
    gstreamer1-plugins-good gstreamer1-plugins-bad-free gstreamer1-plugins-ugly-free \
    ffmpeg mpv yt-dlp qrencode python3-numpy python3-pillow python3-appdirs \
    python3-sortedcontainers python3-falcon python3-mido python3-rtmidi
else
  echo "AVISO: Gestor de paquetes no reconocido. Instala manualmente Python 3.10+, PyQt5, GStreamer 1.0, ffmpeg, mpv y yt-dlp."
fi

echo "==> Copiando DJNova en $APP_HOME ..."
rm -rf "$APP_HOME"
mkdir -p "$APP_HOME" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"
cp -a "$REPO_DIR/lisp" "$REPO_DIR/assets" "$REPO_DIR/djnova.py" "$REPO_DIR/LICENSE" "$APP_HOME/"

# Dependencias Python puras opcionales (ej. qrcode, python-osc)
python3 -m pip install --user --break-system-packages --upgrade qrcode python-osc 2>/dev/null || \
  python3 -m pip install --user --upgrade qrcode python-osc 2>/dev/null || true

cat > "$BIN_DIR/djnova" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_HOME/djnova.py" "\$@"
EOF
chmod +x "$BIN_DIR/djnova"

cp "$REPO_DIR/assets/logo/djnova-256.png" "$ICON_DIR/djnova.png"
sed "s|@EXEC@|$BIN_DIR/djnova|g; s|@ICON@|$ICON_DIR/djnova.png|g" \
  "$REPO_DIR/packaging/linux/djnova.desktop" > "$APPS_DIR/djnova.desktop"

echo ""
echo "✅ ¡DJNova instalado correctamente!"
echo "   Ejecuta 'djnova' en terminal (asegúrate de tener ~/.local/bin en PATH)"
echo "   o ábrelo desde tu menú de aplicaciones: DJNova."
