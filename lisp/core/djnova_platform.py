# -*- coding: utf-8 -*-
"""Capa multiplataforma y utilidades de seguridad para DJNova (Linux & Windows)."""
import json
import logging
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.parse

from PyQt5.QtCore import QStandardPaths, QUrl
from PyQt5.QtGui import QDesktopServices

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


def get_app_root():
    """Devuelve la raíz del proyecto (compatible con PyInstaller _MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    # lisp/core/djnova_platform.py -> <repo_root>
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def get_asset_path(*parts):
    """Resuelve una ruta dentro de assets/ (empaquetado o código fuente)."""
    return os.path.join(get_app_root(), "assets", *parts)


def get_music_dir(subfolder=""):
    """Devuelve la carpeta Música estándar del usuario en Linux o Windows."""
    base = QStandardPaths.writableLocation(QStandardPaths.MusicLocation)
    if not base:
        for cand in ("~/Música", "~/Music", "~"):
            exp = os.path.expanduser(cand)
            if os.path.isdir(exp):
                base = exp
                break
    if not base:
        base = os.path.expanduser("~")
    path = os.path.join(base, subfolder) if subfolder else base
    os.makedirs(path, exist_ok=True)
    return path


def get_bundled_sfx_dir():
    """Devuelve el directorio de SFX incluidos con DJNova (si existe)."""
    p = get_asset_path("sfx")
    return p if os.path.isdir(p) else get_music_dir("Sampler_FX_DJNova")


def get_private_runtime_dir():
    """Directorio temporal privado del usuario (0700 en POSIX) para evitar ataques en /tmp."""
    user_tag = str(os.getuid()) if hasattr(os, "getuid") else (os.environ.get("USERNAME") or "user")
    user_tag = re.sub(r"[^A-Za-z0-9_.-]", "_", user_tag)
    base = tempfile.gettempdir()
    path = os.path.join(base, f"djnova_rt_{user_tag}_{os.getpid()}")
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not IS_WINDOWS:
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass
    return path


def open_folder(path):
    """Abre una carpeta en el explorador nativo del sistema (Dolphin/Nautilus/Explorer)."""
    os.makedirs(path, exist_ok=True)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


def validate_http_url(url):
    """Valida que una URL externa sea http/https segura y no inyecte flags CLI."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or any(ch in u for ch in ("\r", "\n", "\0")):
        return None
    if u.startswith("-"):
        return None
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return u


def validate_rtmp_url(url):
    """Valida URL RTMP/RTMPS evitando inyección de separadores del tee muxer de ffmpeg."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or any(ch in u for ch in ("\r", "\n", "\0", "|", "[", "]")):
        return None
    if u.startswith("-"):
        return None
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme.lower() not in ("rtmp", "rtmps") or not parsed.netloc:
        return None
    return u


def mask_secret(text, secret):
    """Oculta contraseñas o stream keys antes de mostrar logs."""
    if secret and len(secret) >= 3:
        return text.replace(secret, "***")
    return text


def escape_ffmpeg_drawtext_path(path):
    """Escapa rutas para drawtext=textfile='...' de ffmpeg en Linux y Windows."""
    norm = path.replace("\\", "/")
    norm = norm.replace(":", "\\:")
    norm = norm.replace("'", "\\'")
    return norm


def _list_windows_dshow_devices():
    """Devuelve ({'video': [...], 'audio': [...]}) enumerando DirectShow con ffmpeg."""
    videos, audios = [], []
    if not IS_WINDOWS:
        return videos, audios
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=4,
            encoding="utf-8",
            errors="replace",
        )
        mode = None
        for line in proc.stdout.splitlines():
            if "(video)" in line:
                m = re.search(r'"([^"]+)"', line)
                if m:
                    videos.append(m.group(1))
                continue
            if "(audio)" in line:
                m = re.search(r'"([^"]+)"', line)
                if m:
                    audios.append(m.group(1))
                continue
            if "DirectShow video devices" in line:
                mode = "video"
            elif "DirectShow audio devices" in line:
                mode = "audio"
            elif mode and '"' in line and "Alternative name" not in line:
                m = re.search(r'"([^"]+)"', line)
                if m:
                    (videos if mode == "video" else audios).append(m.group(1))
    except Exception as exc:
        logger.debug("No se pudieron listar dispositivos DirectShow: %s", exc)
    return videos, audios


def get_audio_loopback_input_args():
    """Devuelve argumentos de entrada ffmpeg para capturar el audio maestro del sistema."""
    if not IS_WINDOWS:
        try:
            sink = subprocess.check_output(
                ["pactl", "get-default-sink"], text=True, timeout=2
            ).strip()
            src = f"{sink}.monitor" if sink else "default"
        except Exception:
            src = "default"
        return ["-f", "pulse", "-i", src], src

    _, audios = _list_windows_dshow_devices()
    preferred = None
    for dev in audios:
        low = dev.lower()
        if any(k in low for k in ("stereo mix", "mezcla est", "cable output", "virtual", "loopback", "what u hear")):
            preferred = dev
            break
    if preferred is None and audios:
        preferred = audios[0]
    dev_name = preferred or "Stereo Mix"
    return ["-f", "dshow", "-i", f"audio={dev_name}"], f"dshow:{dev_name}"


def list_video_capture_devices():
    """Lista cámaras disponibles en Linux (/dev/video*) o Windows (DirectShow)."""
    if not IS_WINDOWS:
        import glob
        devs = sorted(glob.glob("/dev/video*"))
        return devs if devs else ["/dev/video0"]
    videos, _ = _list_windows_dshow_devices()
    return videos if videos else ["Integrated Camera"]


def get_camera_input_args(device, resolution="1280x720", fps=30):
    """Devuelve argumentos ffmpeg para capturar la cámara según plataforma."""
    if not IS_WINDOWS:
        return [
            "-f", "v4l2", "-input_format", "mjpeg",
            "-video_size", resolution, "-framerate", str(fps),
            "-i", device,
        ]
    return [
        "-f", "dshow",
        "-video_size", resolution, "-framerate", str(fps),
        "-i", f"video={device}",
    ]


def get_available_h264_encoders():
    """Detecta codificadores H.264 disponibles (hardware/software)."""
    encoders = [("libx264 (CPU Universal)", "libx264")]
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"], text=True, timeout=3
        )
        if not IS_WINDOWS and "h264_vaapi" in out and os.path.exists("/dev/dri/renderD128"):
            encoders.append(("h264_vaapi (GPU Hardware Intel/AMD)", "h264_vaapi"))
        if IS_WINDOWS:
            for enc, label in (
                ("h264_nvenc", "h264_nvenc (GPU NVIDIA)"),
                ("h264_qsv", "h264_qsv (GPU Intel QuickSync)"),
                ("h264_amf", "h264_amf (GPU AMD)"),
            ):
                if enc in out:
                    encoders.append((label, enc))
    except Exception:
        pass
    return encoders


def get_mpv_ipc_path():
    """Ruta segura del socket IPC (Unix) o Named Pipe (Windows) para mpv."""
    if IS_WINDOWS:
        return rf"\\.\pipe\djnova_video_stage_{os.getpid()}"
    return os.path.join(get_private_runtime_dir(), "video_stage.sock")


def mpv_ipc_ready(ipc_path):
    """Comprueba si el canal IPC de mpv ya acepta comandos."""
    if IS_WINDOWS:
        return mpv_ipc_send(ipc_path, "get_property", "pid") is not None
    return os.path.exists(ipc_path)


def mpv_ipc_send(ipc_path, *args):
    """Envía un comando JSON IPC a mpv de forma compatible con Linux y Windows."""
    payload = (json.dumps({"command": list(args)}) + "\n").encode("utf-8")
    if IS_WINDOWS:
        try:
            with open(ipc_path, "r+b", buffering=0) as pipe:
                pipe.write(payload)
                raw = pipe.readline()
            return json.loads(raw.decode("utf-8", "replace")) if raw else None
        except Exception:
            return None

    if not os.path.exists(ipc_path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            sock.connect(ipc_path)
            sock.sendall(payload)
            raw = sock.recv(4096).decode("utf-8", "replace").splitlines()
            if raw:
                return json.loads(raw[0])
    except Exception:
        return None
    return None
