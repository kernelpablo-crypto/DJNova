# -*- mode: python ; coding: utf-8 -*-
import glob
import os
import sys

block_cipher = None
ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

datas = [
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "lisp", "i18n"), os.path.join("lisp", "i18n")),
    (os.path.join(ROOT, "lisp", "ui", "themes"), os.path.join("lisp", "ui", "themes")),
    (os.path.join(ROOT, "lisp", "ui", "icons"), os.path.join("lisp", "ui", "icons")),
    (os.path.join(ROOT, "lisp", "default.json"), "lisp"),
    (os.path.join(ROOT, "LICENSE"), "."),
]

# Incluir default.json y todo el árbol de plugins (.py, .json, etc.) para escaneo dinámico os.scandir
for json_file in glob.glob(os.path.join(ROOT, "lisp", "**", "*.json"), recursive=True):
    rel_dir = os.path.relpath(os.path.dirname(json_file), ROOT)
    datas.append((json_file, rel_dir))

for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "lisp", "plugins")):
    dirnames[:] = [d for d in dirnames if d != "__pycache__"]
    rel_dir = os.path.relpath(dirpath, ROOT)
    for fn in filenames:
        if fn.endswith((".pyc", ".json")):
            continue
        datas.append((os.path.join(dirpath, fn), rel_dir))

hiddenimports = [
    "PyQt5.QtSvg",
    "PyQt5.QtXml",
    "gi",
    "gi.repository.Gst",
    "gi.repository.GstBase",
    "gi.repository.GstAudio",
    "gi.repository.GstVideo",
    "gi.repository.GstController",
    "gi.repository.GstPbutils",
    "gi.repository.GstApp",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "falcon",
    "mido",
    "mido.backends",
    "mido.backends.rtmidi",
    "rtmidi",
    "pythonosc",
    "qrcode",
    "PIL",
    "numpy",
    "appdirs",
    "sortedcontainers",
]

# Recoger dinámicamente todos los submódulos de lisp.plugins
for py_file in glob.glob(os.path.join(ROOT, "lisp", "plugins", "**", "*.py"), recursive=True):
    rel = os.path.relpath(py_file, ROOT)[:-3]
    mod = rel.replace(os.sep, ".")
    if mod.endswith(".__init__"):
        mod = mod[:-9]
    if mod not in hiddenimports:
        hiddenimports.append(mod)

# Si se compila en MSYS2/Windows, incluir ffmpeg/ffprobe junto a las DLLs del bundle
binaries = []
msys_prefix = os.environ.get("MINGW_PREFIX", "")
if sys.platform == "win32" and msys_prefix and os.path.isdir(msys_prefix):
    for exe_name in ("ffmpeg.exe", "ffprobe.exe"):
        exe_path = os.path.join(msys_prefix, "bin", exe_name)
        if os.path.isfile(exe_path):
            binaries.append((exe_path, "."))

icon_path = os.path.join(ROOT, "assets", "logo", "djnova.ico")

a = Analysis(
    [os.path.join(ROOT, "djnova.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DJNova",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_path if os.path.exists(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DJNova",
)
