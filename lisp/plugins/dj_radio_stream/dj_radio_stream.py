# -*- coding: utf-8 -*-
import base64
import logging
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lisp.command.layout import LayoutAutoInsertCuesCommand
from lisp.core.djnova_platform import (
    get_audio_loopback_input_args,
    mask_secret,
    validate_http_url,
)
from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState
from lisp.cues.media_cue import MediaCue
from lisp.plugins.gst_backend.gst_backend import GstBackend
from lisp.plugins.gst_backend.gst_media_cue import UriAudioCueFactory

logger = logging.getLogger(__name__)

RADIO_FORMATS = [
    ("mp3_128", "MP3 128 kbps (Estándar compatible)", "libmp3lame", "128k", "audio/mpeg", "mp3"),
    ("mp3_192", "MP3 192 kbps (Alta calidad — Recomendado)", "libmp3lame", "192k", "audio/mpeg", "mp3"),
    ("mp3_320", "MP3 320 kbps (Máxima calidad)", "libmp3lame", "320k", "audio/mpeg", "mp3"),
    ("opus_128", "Opus 128 kbps (Calidad superior / Icecast 2.4+)", "libopus", "128k", "application/ogg", "ogg"),
]


class RadioStreamDialog(QDialog):
    """Non-modal broadcast radio window: Icecast/Shoutcast emitter + online radio cue tuner."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.app = plugin.app
        self.setWindowTitle("Radio Streaming — Emisor Icecast & Sintonizador")
        self.setMinimumWidth(560)
        self.setMinimumHeight(460)

        self._proc = None
        self._streaming = False
        self._start_ts = 0.0
        self._last_title = ""
        self._preview_proc = None

        main_lay = QVBoxLayout(self)

        # Status banner
        banner = QHBoxLayout()
        self.ledLabel = QLabel("● FUERA DEL AIRE", self)
        self.ledLabel.setStyleSheet(
            "background:#141821; border:1px solid #36455E; border-radius:5px; "
            "color:#8A9BB5; font-weight:bold; padding:6px 12px; font-family:monospace;"
        )
        self.timerLabel = QLabel("00:00:00", self)
        self.timerLabel.setObjectName("DJConsoleClock")
        self.timerLabel.setStyleSheet("font-size: 13pt; padding: 4px 10px;")
        self.nowPlayingLbl = QLabel("Ahora suena: —", self)
        self.nowPlayingLbl.setStyleSheet("color:#00D4FF; font-weight:bold;")
        banner.addWidget(self.ledLabel)
        banner.addWidget(self.timerLabel)
        banner.addWidget(self.nowPlayingLbl, 1)
        main_lay.addLayout(banner)

        tabs = QTabWidget(self)
        main_lay.addWidget(tabs, 1)

        # --- TAB 1: EMITTER ---
        tab_emit = QWidget(self)
        emit_lay = QVBoxLayout(tab_emit)
        form = QFormLayout()
        form.setSpacing(6)

        cfg = self.plugin.Config
        self.hostEdit = QLineEdit(str(cfg.get("host", "")), self)
        self.hostEdit.setPlaceholderText("ej. stream.miradio.com o 192.168.1.50")
        form.addRow("Servidor / Host:", self.hostEdit)

        port_mount = QHBoxLayout()
        self.portSpin = QSpinBox(self)
        self.portSpin.setRange(1, 65535)
        self.portSpin.setValue(int(cfg.get("port", 8000)))
        self.portSpin.setFixedWidth(90)
        self.mountEdit = QLineEdit(str(cfg.get("mount", "/live")), self)
        self.mountEdit.setPlaceholderText("/live")
        port_mount.addWidget(self.portSpin)
        port_mount.addWidget(QLabel("Mountpoint:", self))
        port_mount.addWidget(self.mountEdit, 1)
        form.addRow("Puerto:", port_mount)

        auth_row = QHBoxLayout()
        self.userEdit = QLineEdit(str(cfg.get("user", "source")), self)
        self.userEdit.setFixedWidth(120)
        self.passEdit = QLineEdit(str(cfg.get("password", "")), self)
        self.passEdit.setEchoMode(QLineEdit.Password)
        self.passEdit.setPlaceholderText("Contraseña de emisión (source)")
        btn_eye = QPushButton("👁", self)
        btn_eye.setCheckable(True)
        btn_eye.setFixedWidth(32)
        btn_eye.toggled.connect(
            lambda ch: self.passEdit.setEchoMode(QLineEdit.Normal if ch else QLineEdit.Password)
        )
        auth_row.addWidget(self.userEdit)
        auth_row.addWidget(QLabel("Clave:", self))
        auth_row.addWidget(self.passEdit, 1)
        auth_row.addWidget(btn_eye)
        form.addRow("Usuario:", auth_row)

        self.fmtCombo = QComboBox(self)
        saved_fmt = cfg.get("format", "mp3_192")
        sel_idx = 1
        for i, item in enumerate(RADIO_FORMATS):
            self.fmtCombo.addItem(item[1], item[0])
            if item[0] == saved_fmt:
                sel_idx = i
        self.fmtCombo.setCurrentIndex(sel_idx)
        form.addRow("Calidad / Códec:", self.fmtCombo)

        self.nameEdit = QLineEdit(str(cfg.get("station_name", "Cabina DJ en Vivo")), self)
        form.addRow("Nombre Emisora:", self.nameEdit)

        self.genreEdit = QLineEdit(str(cfg.get("genre", "DJ / Live")), self)
        form.addRow("Género:", self.genreEdit)

        emit_lay.addLayout(form)

        opts_row = QHBoxLayout()
        self.chkReconnect = QCheckBox("Reconexión automática si se corta la red", self)
        self.chkReconnect.setChecked(bool(cfg.get("auto_reconnect", True)))
        self.chkMeta = QCheckBox("Enviar título de pista automáticamente (Icecast)", self)
        self.chkMeta.setChecked(bool(cfg.get("send_metadata", True)))
        opts_row.addWidget(self.chkReconnect)
        opts_row.addWidget(self.chkMeta)
        emit_lay.addLayout(opts_row)

        self.logView = QTextEdit(self)
        self.logView.setReadOnly(True)
        self.logView.setMaximumHeight(110)
        self.logView.setStyleSheet("font-family: monospace; font-size: 8.5pt;")
        emit_lay.addWidget(self.logView)

        emit_btns = QHBoxLayout()
        self.btnAir = QPushButton("● SALIR AL AIRE (RADIO)", self)
        self.btnAir.setCheckable(True)
        self.btnAir.setFixedHeight(38)
        self.btnAir.setStyleSheet(
            """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00C864, stop:1 #007A3D);
                border: 1px solid #00FF9D; color: #FFFFFF; font-weight: bold; font-size: 10pt;
            }
            QPushButton:checked {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E51C23, stop:1 #8B0000);
                border: 2px solid #FFFFFF; color: #FFFFFF;
            }
            """
        )
        self.btnAir.toggled.connect(self._on_air_toggled)
        emit_btns.addWidget(self.btnAir, 1)
        emit_lay.addLayout(emit_btns)

        tabs.addTab(tab_emit, "📡 Emitir a Radio Online (Icecast)")

        # --- TAB 2: TUNER / RECEIVE STREAM AS CUE ---
        tab_rx = QWidget(self)
        rx_lay = QVBoxLayout(tab_rx)
        rx_info = QLabel(
            "Pega el enlace de cualquier radio online (Icecast, Shoutcast, .mp3, .aac, .m3u8) "
            "para escucharla en directo o insertarla como un Cue en tu sesión:",
            self,
        )
        rx_info.setWordWrap(True)
        rx_info.setStyleSheet("color: #00D4FF; font-weight: bold;")
        rx_lay.addWidget(rx_info)

        rx_form = QFormLayout()
        self.rxUrlEdit = QLineEdit(self)
        self.rxUrlEdit.setPlaceholderText("https://ejemplo.com:8000/stream.mp3")
        self.rxNameEdit = QLineEdit(self)
        self.rxNameEdit.setPlaceholderText("Mi Radio Online")
        rx_form.addRow("URL del Stream:", self.rxUrlEdit)
        rx_form.addRow("Nombre del Cue:", self.rxNameEdit)
        rx_lay.addLayout(rx_form)

        rx_btns = QHBoxLayout()
        btn_add_cue = QPushButton("➕ Insertar Radio como Cue en la Sesión", self)
        btn_add_cue.setStyleSheet("background:#007A3D; border:1px solid #00E676; font-weight:bold; padding:8px;")
        btn_add_cue.clicked.connect(self._insert_radio_cue)

        self.btnPreview = QPushButton("🎧 Escuchar Preview", self)
        self.btnPreview.setCheckable(True)
        self.btnPreview.toggled.connect(self._toggle_preview)

        rx_btns.addWidget(btn_add_cue, 1)
        rx_btns.addWidget(self.btnPreview)
        rx_lay.addLayout(rx_btns)
        rx_lay.addStretch(1)

        tabs.addTab(tab_rx, "📻 Sintonizar Radio como Cue")

        # Periodic status/metadata/reconnect timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.logView.append(f"[{ts}] {msg}")

    def _save_cfg(self):
        cfg = self.plugin.Config
        cfg["host"] = self.hostEdit.text().strip()
        cfg["port"] = int(self.portSpin.value())
        cfg["mount"] = self.mountEdit.text().strip() or "/live"
        cfg["user"] = self.userEdit.text().strip() or "source"
        cfg["password"] = self.passEdit.text()
        cfg["format"] = self.fmtCombo.currentData()
        cfg["station_name"] = self.nameEdit.text().strip()
        cfg["genre"] = self.genreEdit.text().strip()
        cfg["auto_reconnect"] = self.chkReconnect.isChecked()
        cfg["send_metadata"] = self.chkMeta.isChecked()
        cfg.write()

    def _on_air_toggled(self, checked):
        if checked:
            self._start_stream()
        else:
            self._stop_stream()

    def _start_stream(self):
        host = self.hostEdit.text().strip()
        password = self.passEdit.text()
        if not host or any(ch in host for ch in ("/", "\\", "@", " ", "\r", "\n", "\0")):
            QMessageBox.warning(self, "Host inválido", "Ingresa un Servidor / Host válido de tu radio Icecast (sin espacios ni barras).")
            self.btnAir.setChecked(False)
            return
        self._save_cfg()

        mount = self.mountEdit.text().strip() or "/live"
        if not mount.startswith("/"):
            mount = "/" + mount
        user = urllib.parse.quote(self.userEdit.text().strip() or "source", safe="")
        pwd = urllib.parse.quote(password, safe="")
        port = int(self.portSpin.value())
        ice_url = f"icecast://{user}:{pwd}@{host}:{port}{mount}"

        fmt_key = self.fmtCombo.currentData()
        fmt_info = next((f for f in RADIO_FORMATS if f[0] == fmt_key), RADIO_FORMATS[1])
        _, _, acodec, bitrate, ctype, mux = fmt_info

        in_args, src_desc = get_audio_loopback_input_args()
        cmd = [
            "ffmpeg", "-re",
            *in_args,
            "-c:a", acodec, "-b:a", bitrate, "-ac", "2", "-ar", "44100",
            "-content_type", ctype,
            "-ice_name", self.nameEdit.text().strip() or "Cabina DJNova",
            "-ice_genre", self.genreEdit.text().strip() or "Live",
            "-ice_public", "0",
            "-f", mux,
            ice_url,
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._streaming = True
            self._start_ts = time.monotonic()
            self._last_title = ""
            self.btnAir.setText("■ CORTAR TRANSMISIÓN DE RADIO")
            self.ledLabel.setText("● AL AIRE (RADIO)")
            self.ledLabel.setStyleSheet(
                "background:#2A0B10; border:2px solid #FF2A4B; border-radius:5px; "
                "color:#FF5252; font-weight:bold; padding:6px 12px; font-family:monospace;"
            )
            self._log(f"Conectando a icecast://{host}:{port}{mount} ({bitrate}) desde {src_desc}...")
        except Exception as exc:
            self._streaming = False
            self.btnAir.setChecked(False)
            self._log(f"ERROR iniciando ffmpeg: {mask_secret(str(exc), password)}")

    def _stop_stream(self):
        self._streaming = False
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self.btnAir.setText("● SALIR AL AIRE (RADIO)")
        self.ledLabel.setText("● FUERA DEL AIRE")
        self.ledLabel.setStyleSheet(
            "background:#141821; border:1px solid #36455E; border-radius:5px; "
            "color:#8A9BB5; font-weight:bold; padding:6px 12px; font-family:monospace;"
        )
        self.timerLabel.setText("00:00:00")
        self._log("Transmisión de radio detenida.")

    def _current_running_title(self):
        if self.app.session is None or self.app.layout is None:
            return ""
        try:
            for cue in self.app.layout.cues(MediaCue):
                if cue.state & CueState.Running:
                    return cue.name or ""
        except Exception:
            pass
        return ""

    def _push_icecast_metadata(self, title):
        host = self.hostEdit.text().strip()
        port = int(self.portSpin.value())
        mount = self.mountEdit.text().strip() or "/live"
        if not mount.startswith("/"):
            mount = "/" + mount
        user = self.userEdit.text().strip() or "source"
        pwd = self.passEdit.text()

        def _worker():
            try:
                qs = urllib.parse.urlencode({"mount": mount, "mode": "updinfo", "song": title})
                url = f"http://{host}:{port}/admin/metadata?{qs}"
                token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
                req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
                with urllib.request.urlopen(req, timeout=3):
                    pass
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _tick(self):
        cur = self._current_running_title()
        self.nowPlayingLbl.setText(f"Ahora suena: {cur or '—'}")

        if self._streaming:
            if self._proc and self._proc.poll() is not None:
                rc = self._proc.returncode
                self._log(f"Conexión cerrada por el servidor (código {rc}).")
                self._proc = None
                if self.chkReconnect.isChecked():
                    self._log("Reintentando conexión automática...")
                    self._start_stream()
                else:
                    self.btnAir.setChecked(False)
                return

            secs = max(0, int(time.monotonic() - self._start_ts))
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            self.timerLabel.setText(f"{h:02d}:{m:02d}:{s:02d}")

            if self.chkMeta.isChecked() and cur and cur != self._last_title:
                self._last_title = cur
                self._push_icecast_metadata(cur)
                self._log(f"Metadatos enviados: {cur}")

    def _insert_radio_cue(self):
        url = validate_http_url(self.rxUrlEdit.text())
        if not url:
            QMessageBox.warning(self, "URL no válida", "Ingresa una URL de radio válida que empiece con http:// o https://")
            return
        if self.app.session is None or self.app.layout is None:
            QMessageBox.warning(self, "Sin sesión", "Abre o crea una sesión primero para insertar la radio.")
            return
        try:
            factory = UriAudioCueFactory(GstBackend.Config["pipeline"])
            cue = factory(self.app, uri=url)
            cue.name = self.rxNameEdit.text().strip() or f"Radio: {url}"
            self.app.commands_stack.do(LayoutAutoInsertCuesCommand(self.app.session.layout, cue))
            QMessageBox.information(self, "Radio agregada", f"Se agregó '{cue.name}' a tu sesión actual.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"No se pudo agregar el stream como Cue:\n{exc}")

    def _toggle_preview(self, checked):
        if self._preview_proc is not None:
            try:
                self._preview_proc.terminate()
            except Exception:
                pass
            self._preview_proc = None

        if checked:
            url = validate_http_url(self.rxUrlEdit.text())
            if not url:
                self.btnPreview.setChecked(False)
                return
            try:
                self._preview_proc = subprocess.Popen(
                    ["mpv", "--no-video", "--quiet", "--", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.btnPreview.setText("■ Detener Preview")
            except Exception:
                self.btnPreview.setChecked(False)
        else:
            self.btnPreview.setText("🎧 Escuchar Preview")

    def shutdown(self):
        self._timer.stop()
        self._stop_stream()
        self._toggle_preview(False)


class DjRadioStream(Plugin):
    """Plugin de Radio Streaming (Emisor Icecast + Sintonizador de Radios Online)."""

    Name = "Radio Streaming (Icecast / Online)"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Permite transmitir el audio maestro de tu consola a una radio Icecast/Shoutcast "
        "con envío automático de metadatos, y sintonizar radios online como Cues."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        self.dialog = None
        self.action = QAction("Radio Streaming (Al Aire / Sintonizar)...", self.app.window.menuTools)
        self.action.triggered.connect(self.open_dialog)
        self.app.window.menuTools.addAction(self.action)

        self._shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self.app.window)
        self._shortcut.activated.connect(self.open_dialog)

    def open_dialog(self):
        if self.dialog is None:
            self.dialog = RadioStreamDialog(self, self.app.window)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def finalize(self):
        if self._shortcut is not None:
            self._shortcut.setEnabled(False)
        if self.dialog is not None:
            self.dialog.shutdown()
            self.dialog.close()
        if self.action is not None:
            self.app.window.menuTools.removeAction(self.action)
        super().finalize()
