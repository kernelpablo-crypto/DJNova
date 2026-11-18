# -*- coding: utf-8 -*-
import logging
import os
import re
import subprocess
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QVBoxLayout,
)

from lisp import plugins
from lisp.command.layout import LayoutAutoInsertCuesCommand
from lisp.core.djnova_platform import get_music_dir, open_folder, validate_http_url
from lisp.core.plugin import Plugin
from lisp.plugins.gst_backend.gst_backend import GstBackend
from lisp.plugins.gst_backend.gst_media_cue import UriAudioCueFactory

logger = logging.getLogger(__name__)

DEFAULT_DL_DIR = get_music_dir("Descargas_YouTube_DJNova")
PCT_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")

FORMAT_OPTIONS = [
    ("mp3_320", "MP3 320 kbps — Solo Audio, máxima calidad (Recomendado)"),
    ("best_fast", "Audio Original (Opus/M4A) — Descarga ultrarrápida (2-4 seg)"),
    ("flac", "FLAC Lossless — Audio sin pérdida"),
    ("wav", "WAV Sin Comprimir — Cero latencia de decodificación"),
    ("video_hd", "Video + Audio HD (.mp4 1080p/720p) — Para Pantalla de Escenario"),
]


class YoutubeDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str, str)  # filepath, clean_title
    failed = pyqtSignal(str)

    def __init__(self, url, fmt_key, out_dir):
        super().__init__()
        self.url = url.strip()
        self.fmt_key = fmt_key
        self.out_dir = out_dir
        self._proc = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            out_tmpl = os.path.join(self.out_dir, "%(title).120B [%(id)s].%(ext)s")

            if self.fmt_key == "video_hd":
                cmd = [
                    "yt-dlp",
                    "--no-playlist",
                    "--no-simulate",
                    "--progress",
                    "--newline",
                    "--restrict-filenames",
                    "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
                    "--merge-output-format", "mp4",
                    "-o", out_tmpl,
                    "--print", "after_move:filepath",
                    "--print", "before_dl:__TITLE__:%(title)s",
                ]
            else:
                cmd = [
                    "yt-dlp",
                    "--no-playlist",
                    "--no-simulate",
                    "--progress",
                    "--newline",
                    "--restrict-filenames",
                    "-f", "bestaudio/best",
                    "-x",
                    "-o", out_tmpl,
                    "--print", "after_move:filepath",
                    "--print", "before_dl:__TITLE__:%(title)s",
                ]

                if self.fmt_key == "mp3_320":
                    cmd += ["--audio-format", "mp3", "--audio-quality", "0"]
                elif self.fmt_key == "flac":
                    cmd += ["--audio-format", "flac"]
                elif self.fmt_key == "wav":
                    cmd += ["--audio-format", "wav"]
                else:
                    # best_fast: keep best original audio stream without re-encoding
                    cmd += ["--audio-format", "best"]

            cmd.extend(["--", self.url])

            self.progress.emit(5, "Obteniendo información del enlace...")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            clean_title = ""
            final_path = ""
            last_lines = []

            for raw_line in self._proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                last_lines.append(line)
                if len(last_lines) > 15:
                    last_lines.pop(0)

                if line.startswith("__TITLE__:"):
                    clean_title = line[len("__TITLE__:"):].strip()
                    self.progress.emit(12, f"Descargando: {clean_title[:55]}...")
                    continue

                m = PCT_RE.search(line)
                if m:
                    pct = int(min(90, max(12, float(m.group(1)) * 0.8 + 10)))
                    self.progress.emit(pct, f"Descargando audio... {m.group(1)}%")
                    continue

                if "[ExtractAudio]" in line or "Post-process" in line:
                    self.progress.emit(92, "Convirtiendo audio en máxima calidad (ffmpeg)...")
                    continue

                if os.path.isabs(line) and os.path.exists(line):
                    final_path = line

            rc = self._proc.wait()
            if self._cancelled:
                self.failed.emit("Descarga cancelada por el usuario.")
                return

            if rc != 0 or not final_path or not os.path.exists(final_path):
                err_tail = "\n".join(last_lines[-4:])
                self.failed.emit(f"yt-dlp terminó con error (código {rc}).\n{err_tail}")
                return

            if not clean_title:
                clean_title = os.path.splitext(os.path.basename(final_path))[0]

            self.progress.emit(100, "¡Descarga lista!")
            self.finished_ok.emit(final_path, clean_title)
        except Exception as exc:
            logger.exception("Error descargando audio con yt-dlp")
            self.failed.emit(str(exc))


class YoutubeDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.app = plugin.app
        self.worker = None

        self.setWindowTitle("YouTube → Audio Cue al Instante")
        self.setMinimumWidth(520)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        header = QLabel("Pega un enlace de YouTube (o SoundCloud) para bajar solo el audio en máxima calidad:", self)
        header.setStyleSheet("font-weight: bold; color: #00D4FF;")
        lay.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)

        # URL input with clipboard auto-detect
        url_row = QHBoxLayout()
        self.urlEdit = QLineEdit(self)
        self.urlEdit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        cb_text = (QApplication.clipboard().text() or "").strip()
        if cb_text.startswith(("http://", "https://")) and any(
            k in cb_text.lower() for k in ("youtube.com", "youtu.be", "music.youtube", "soundcloud.com")
        ):
            self.urlEdit.setText(cb_text)
        btn_paste = QPushButton("Pegar", self)
        btn_paste.setFixedWidth(64)
        btn_paste.clicked.connect(lambda: self.urlEdit.setText((QApplication.clipboard().text() or "").strip()))
        url_row.addWidget(self.urlEdit, 1)
        url_row.addWidget(btn_paste)
        form.addRow("Enlace:", url_row)

        # Format combo
        self.fmtCombo = QComboBox(self)
        saved_fmt = self.plugin.Config.get("format", "mp3_320")
        sel_idx = 0
        for i, (key, label) in enumerate(FORMAT_OPTIONS):
            self.fmtCombo.addItem(label, key)
            if key == saved_fmt:
                sel_idx = i
        self.fmtCombo.setCurrentIndex(sel_idx)
        form.addRow("Calidad:", self.fmtCombo)

        # Destination combo
        self.destCombo = QComboBox(self)
        self.destCombo.addItem("Insertar en la sesión actual (Lista / Cart)", "session")
        self.destCombo.addItem("Enviar a Pantalla de Escenario (Video)", "video_stage")
        for i in range(8):
            self.destCombo.addItem(f"Enviar al Pad Sampler FX [{i + 1}]", f"fx_{i}")
        self.destCombo.addItem("Solo guardar archivo en carpeta", "folder_only")
        form.addRow("Destino:", self.destCombo)

        lay.addLayout(form)

        self.chkAutoMaster = QCheckBox("Aplicar Auto-Master inteligente (EQ 10B + Compresión + Nivel -14 dB) al terminar", self)
        self.chkAutoMaster.setChecked(bool(self.plugin.Config.get("auto_master", True)))
        lay.addWidget(self.chkAutoMaster)

        self.statusLabel = QLabel("Listo para descargar.", self)
        self.statusLabel.setStyleSheet("color: #8A9BB5;")
        lay.addWidget(self.statusLabel)

        self.progressBar = QProgressBar(self)
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        lay.addWidget(self.progressBar)

        btns = QHBoxLayout()
        self.btnFolder = QPushButton("Abrir carpeta", self)
        self.btnFolder.clicked.connect(self.plugin.open_download_folder)
        btns.addWidget(self.btnFolder)
        btns.addStretch(1)

        self.btnDownload = QPushButton("⬇ Descargar y Agregar", self)
        self.btnDownload.setStyleSheet(
            """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E51C23, stop:1 #990D14);
                border: 1px solid #FF5252; color: #FFFFFF; font-weight: bold; padding: 6px 14px;
            }
            QPushButton:hover { background-color: #FF2A4B; }
            """
        )
        self.btnDownload.clicked.connect(self._start_download)
        btns.addWidget(self.btnDownload)

        self.btnClose = QPushButton("Cerrar", self)
        self.btnClose.clicked.connect(self._on_close)
        btns.addWidget(self.btnClose)
        lay.addLayout(btns)

    def _start_download(self):
        url = validate_http_url(self.urlEdit.text())
        if not url:
            QMessageBox.warning(self, "Enlace no válido", "Ingresa un enlace http:// o https:// válido.")
            return

        fmt_key = self.fmtCombo.currentData()
        self.plugin.Config["format"] = fmt_key
        self.plugin.Config["auto_master"] = self.chkAutoMaster.isChecked()
        self.plugin.Config.write()

        dl_dir = self.plugin.Config.get("download_dir") or DEFAULT_DL_DIR
        self.btnDownload.setEnabled(False)
        self.progressBar.setValue(2)

        self.worker = YoutubeDownloadWorker(url, fmt_key, dl_dir)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished_ok)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, pct, msg):
        self.progressBar.setValue(pct)
        self.statusLabel.setText(msg)

    def _on_finished_ok(self, filepath, clean_title):
        dest = self.destCombo.currentData()

        if dest == "session":
            if self.app.session is not None and self.app.layout is not None:
                try:
                    factory = UriAudioCueFactory(GstBackend.Config["pipeline"])
                    cue = factory(self.app, uri=filepath)
                    cue.name = clean_title
                    self.app.commands_stack.do(
                        LayoutAutoInsertCuesCommand(self.app.session.layout, cue)
                    )

                    if self.chkAutoMaster.isChecked():
                        self.statusLabel.setText("Aplicando Auto-Master (EQ + Compresión + Nivel)...")
                        try:
                            am = plugins.get_plugin("DjAutoMaster")
                            if am is not None:
                                am.auto_master_single_silent(
                                    cue,
                                    on_done=lambda res, err: self._on_automaster_done(clean_title, res, err),
                                )
                                return
                        except Exception:
                            logger.exception("No se pudo ejecutar Auto-Master automático")

                    self.statusLabel.setStyleSheet("color: #00E676; font-weight: bold;")
                    self.statusLabel.setText(f"✔ ¡Agregado a tu sesión!: {clean_title}")
                except Exception as exc:
                    logger.exception("Error insertando cue desde YouTube")
                    self.statusLabel.setStyleSheet("color: #FFB300;")
                    self.statusLabel.setText(f"Guardado en disco, pero no se pudo insertar en sesión: {exc}")
            else:
                self.statusLabel.setStyleSheet("color: #00E676; font-weight: bold;")
                self.statusLabel.setText(f"✔ Guardado (abre una sesión para insertar): {os.path.basename(filepath)}")

        elif dest == "video_stage":
            try:
                vs = plugins.get_plugin("DjVideoStage")
                if vs is not None:
                    vs.add_video_file(filepath)
                    vs.open_dialog()
                    self.statusLabel.setStyleSheet("color: #00E676; font-weight: bold;")
                    self.statusLabel.setText(f"✔ ¡Agregado a Pantalla de Escenario!: {clean_title}")
            except Exception as exc:
                self.statusLabel.setText(f"Guardado en disco (error enviando a Escenario): {exc}")

        elif dest.startswith("fx_"):
            idx = int(dest.split("_")[1])
            try:
                smp = plugins.get_plugin("DjSampler")
                if smp and smp.sampler_widget and 0 <= idx < len(smp.sampler_widget.pads):
                    pad = smp.sampler_widget.pads[idx]
                    pad.pad_data["path"] = filepath
                    pad.pad_data["name"] = clean_title[:14]
                    pad._refresh_ui()
                    smp.sampler_widget.save_pads()
                    self.statusLabel.setStyleSheet("color: #00E676; font-weight: bold;")
                    self.statusLabel.setText(f"✔ ¡Asignado al Pad F{idx + 1}!: {clean_title}")
            except Exception as exc:
                self.statusLabel.setText(f"Guardado en disco (error asignando pad): {exc}")
        else:
            self.statusLabel.setStyleSheet("color: #00E676; font-weight: bold;")
            self.statusLabel.setText(f"✔ Audio guardado en: {os.path.basename(filepath)}")

        self.btnDownload.setEnabled(True)
        self.urlEdit.clear()

    def _on_automaster_done(self, title, res, err):
        self.btnDownload.setEnabled(True)
        self.urlEdit.clear()
        if res is not None:
            self.statusLabel.setStyleSheet("color: #00E676; font-weight: bold;")
            self.statusLabel.setText(
                f"✔ ¡Listo! '{title[:36]}' — {res['profile']} ({res['gain_db']:+.1f} dB + EQ + Comp)"
            )
        else:
            self.statusLabel.setStyleSheet("color: #00E676;")
            self.statusLabel.setText(f"✔ Agregado a la sesión: {title} (Auto-Master omitido)")

    def _on_failed(self, err_msg):
        self.btnDownload.setEnabled(True)
        self.progressBar.setValue(0)
        self.statusLabel.setStyleSheet("color: #FF4766; font-weight: bold;")
        self.statusLabel.setText("✖ Error en la descarga.")
        QMessageBox.critical(self, "Error descargando desde YouTube", err_msg)

    def _on_close(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
        self.accept()


class DjYoutubeDl(Plugin):
    """Descargador instantáneo de audio desde YouTube en máxima calidad directo a Cue / Pad FX."""

    Name = "Descargador YouTube → Audio Cue"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Pega un enlace de YouTube para descargar solo el audio en máxima calidad (MP3 320k / FLAC / WAV / Opus) "
        "en segundo plano, aplicar Auto-Master y agregarlo al instante como Cue listo para sonar."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        self._bar_btn = None
        self._shortcut = None

        window = self.app.window
        self.menu = QMenu("YouTube → Audio Cue", window.menuTools)

        act_dl = QAction("Descargar audio desde enlace de YouTube... (Ctrl+Y)", self.menu)
        act_dl.triggered.connect(self.open_dialog)
        self.menu.addAction(act_dl)

        act_dir = QAction("Abrir carpeta de descargas YouTube", self.menu)
        act_dir.triggered.connect(self.open_download_folder)
        self.menu.addAction(act_dir)

        window.menuTools.addMenu(self.menu)

        self._shortcut = QShortcut(QKeySequence("Ctrl+Y"), window)
        self._shortcut.activated.connect(self.open_dialog)

        # Add compact YT+ button to the Consola DJ top bar once plugins are loaded
        QTimer.singleShot(450, self._attach_console_button)

    def _attach_console_button(self):
        try:
            djc = plugins.get_plugin("DjConsole")
            if djc and djc.console_widget:
                w = djc.console_widget
                btn = QPushButton("YT+", w)
                btn.setFixedWidth(40)
                btn.setToolTip("Descargar audio de YouTube en máxima calidad y agregarlo a la sesión (Ctrl+Y)")
                btn.setStyleSheet(
                    """
                    QPushButton {
                        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #CC181E, stop:1 #850B10);
                        border: 1px solid #FF5252; border-radius: 4px;
                        color: #FFFFFF; font-weight: bold; font-size: 8.5pt; padding: 4px 3px;
                    }
                    QPushButton:hover { background-color: #FF2A4B; }
                    """
                )
                btn.clicked.connect(self.open_dialog)
                # Insert right after btnRec (index 2)
                w.layout().insertWidget(2, btn)
                self._bar_btn = btn
        except Exception:
            pass

    def open_dialog(self):
        dlg = YoutubeDialog(self, self.app.window)
        dlg.exec_()

    def open_download_folder(self):
        dl_dir = self.plugin.Config.get("download_dir") if hasattr(self, "plugin") else self.Config.get("download_dir")
        open_folder(dl_dir or DEFAULT_DL_DIR)

    def finalize(self):
        if self._shortcut is not None:
            self._shortcut.setEnabled(False)
        if self._bar_btn is not None:
            self._bar_btn.deleteLater()
        if self.menu is not None:
            self.app.window.menuTools.removeAction(self.menu.menuAction())
        super().finalize()
