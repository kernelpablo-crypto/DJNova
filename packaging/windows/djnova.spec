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

# Incluir default.json y módulos dinámicos de todos los plugins y elementos GStreamer
for json_file in glob.glob(os.path.join(ROOT, "lisp", "**", "*.json"), recursive=True):
    rel_dir = os.path.relpath(os.path.dirname(json_file), ROOT)
    datas.append((json_file, rel_dir))

hiddenimports = [
    "gi",
    "gi.repository.Gst",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "falcon",
    "mido",
    "rtmidi",
    "pythonosc",
    "qrcode",
    "PIL",
    "numpy",
    "appdirs",
    "sortedcontainers",
]

# Recoger dinámicamente todos los submódulos de lisp.plugins y lisp.backend
for py_file in glob.glob(os.path.join(ROOT, "lisp", "plugins", "**", "*.py"), recursive=True):
    rel = os.path.relpath(py_file, ROOT)[:-3]
    mod = rel.replace(os.sep, ".")
    if mod.endswith(".__init__"):
        mod = mod[:-9]
    if mod not in hiddenimports:
        hiddenimports.append(mod)

# Si se compila en MSYS2/Windows, incluir binarios y plugins de GStreamer si existen
binaries = []
msys_prefix = os.environ.get("MINGW_PREFIX", "")
if sys.platform == "win32" and msys_prefix and os.path.isdir(msys_prefix):
    gst_plugins_dir = os.path.join(msys_prefix, "lib", "gstreamer-1.0")
    if os.path.isdir(gst_plugins_dir):
        datas.append((gst_plugins_dir, os.path.join("lib", "gstreamer-1.0")))
    typelib_dir = os.path.join(msys_prefix, "lib", "girepository-1.0")
    if os.path.isdir(typelib_dir):
        datas.append((typelib_dir, os.path.join("lib", "girepository-1.0")))

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
    upx=True,
    console=False,
    icon=icon_path if os.path.exists(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DJNova",
)
