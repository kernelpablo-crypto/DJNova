# -*- mode: python ; coding: utf-8 -*-
import glob
import os

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

for py_file in glob.glob(os.path.join(ROOT, "lisp", "plugins", "**", "*.py"), recursive=True):
    rel = os.path.relpath(py_file, ROOT)[:-3]
    mod = rel.replace(os.sep, ".")
    if mod.endswith(".__init__"):
        mod = mod[:-9]
    if mod not in hiddenimports:
        hiddenimports.append(mod)

a = Analysis(
    [os.path.join(ROOT, "djnova.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="DJNova",
)
