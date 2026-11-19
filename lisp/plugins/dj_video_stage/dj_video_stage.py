# -*- coding: utf-8 -*-
import json
import logging
import os
import socket
import subprocess
import time

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QSlider,
    QVBoxLayout,
)

from lisp.core.djnova_platform import (
    IS_WINDOWS,
    get_mpv_ipc_path,
    get_music_dir,
    mpv_ipc_ready,
    mpv_ipc_send,
)
from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState
from lisp.cues.media_cue import MediaCue
from lisp.ui.icons import IconTheme

logger = logging.getLogger(__name__)

IPC_SOCK = get_mpv_ipc_path()
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".flv"}


def get_cue_video_path(cue):
    """Devuelve la ruta local del video si el cue apunta a un archivo de video válido."""
    if not isinstance(cue, MediaCue):
        return ""
    try:
        inp = cue.media.input_uri() if hasattr(cue.media, "input_uri") else None
        if inp is None and hasattr(cue.media, "element"):
            uri_el = cue.media.element("UriInput")
            if uri_el is not None and hasattr(uri_el, "input_uri"):
                inp = uri_el.input_uri()
        if inp is None:
            return ""
        path = getattr(inp, "absolute_path", None) or getattr(inp, "path", None)
        if not path and isinstance(inp, str):
            path = inp
        if not path:
            return ""
        ext = os.path.splitext(str(path))[1].lower()
        if ext in VIDEO_EXTENSIONS and os.path.exists(str(path)):
            return str(path)
    except Exception:
        pass
    return ""


class VideoCueBridge(QObject):
    """Puente seguro entre hilos de reproducción de Cues y la ventana mpv en el hilo Qt."""

    cue_started = pyqtSignal(object)
    cue_paused = pyqtSignal(object)
    cue_stopped = pyqtSignal(object)


def fmt_sec(val):
    if val is None or val < 0:
        return "00:00"
    s = int(val)
    return f"{s // 60:02d}:{s % 60:02d}"


class VideoStageDialog(QDialog):
    """Non-modal Video Cues & Stage / Projector Screen controller powered by mpv IPC."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("Video y Pantalla de Escenario / Proyector")
        self.setMinimumWidth(620)
        self.setMinimumHeight(460)

        self._mpv_proc = None
        self._active_file = ""
        self._blackout = False
        self._synced_cue = None
        self._auto_opened = False

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        # Top Stage Status Bar
        top = QHBoxLayout()
        self.stageLed = QLabel("● PANTALLA CERRADA", self)
        self.stageLed.setStyleSheet(
            "background:#141821; border:1px solid #36455E; border-radius:5px; "
            "color:#8A9BB5; font-weight:bold; padding:5px 10px; font-family:monospace;"
        )
        self.btnOpenStage = QPushButton("🖥 Abrir Pantalla de Escenario", self)
        self.btnOpenStage.setCheckable(True)
        self.btnOpenStage.setStyleSheet("background:#005A78; border:1px solid #00D4FF; font-weight:bold; padding:6px 10px;")
        self.btnOpenStage.toggled.connect(self._toggle_stage_window)

        self.btnFullscreen = QPushButton("⛶ Pantalla Completa", self)
        self.btnFullscreen.clicked.connect(self._toggle_fullscreen)

        top.addWidget(self.stageLed)
        top.addStretch(1)
        top.addWidget(self.btnOpenStage)
        top.addWidget(self.btnFullscreen)
        lay.addLayout(top)

        # Active Video Monitor Info
        info_frame = QFrame(self)
        info_frame.setStyleSheet("background:#0B0E14; border:1px solid #2E3C54; border-radius:6px; padding:6px;")
        info_lay = QVBoxLayout(info_frame)
        info_lay.setSpacing(4)

        row_meta = QHBoxLayout()
        self.lblCurrent = QLabel("En pantalla: (Sin video)", self)
        self.lblCurrent.setStyleSheet("color:#00D4FF; font-weight:bold; border:none;")
        self.lblTime = QLabel("00:00 / 00:00", self)
        self.lblTime.setStyleSheet("font-family:monospace; font-weight:bold; color:#00E676; border:none;")
        row_meta.addWidget(self.lblCurrent, 1)
        row_meta.addWidget(self.lblTime)
        info_lay.addLayout(row_meta)

        self.progressBar = QProgressBar(self)
        self.progressBar.setRange(0, 1000)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(False)
        self.progressBar.setFixedHeight(10)
        info_lay.addWidget(self.progressBar)
        lay.addWidget(info_frame)

        # Transport Controls
        ctrl = QHBoxLayout()
        btn_play = QPushButton(IconTheme.get("cue-start"), "PLAY", self)
        btn_play.clicked.connect(self.play_selected)
        btn_pause = QPushButton(IconTheme.get("cue-pause"), "PAUSA", self)
        btn_pause.clicked.connect(self.toggle_pause)
        btn_stop = QPushButton(IconTheme.get("cue-stop"), "STOP", self)
        btn_stop.setObjectName("DJBtnPanic")
        btn_stop.clicked.connect(self.stop_video)

        self.btnBlackout = QPushButton("⬛ BLACKOUT", self)
        self.btnBlackout.setCheckable(True)
        self.btnBlackout.setToolTip("Pone la pantalla del proyector completamente en negro")
        self.btnBlackout.toggled.connect(self._toggle_blackout)

        btn_logo = QPushButton("🖼 Mostrar Logo", self)
        btn_logo.setToolTip("Muestra la imagen/flyer de espera en el proyector")
        btn_logo.clicked.connect(self.show_standby_image)

        ctrl.addWidget(btn_play)
        ctrl.addWidget(btn_pause)
        ctrl.addWidget(btn_stop)
        ctrl.addWidget(self.btnBlackout)
        ctrl.addWidget(btn_logo)
        lay.addLayout(ctrl)

        # Options Row (Loop, Mute for Visuals, Volume, Choose Logo)
        opts = QHBoxLayout()
        self.chkLoop = QCheckBox("Bucle (Visuales DJ)", self)
        self.chkLoop.setChecked(bool(self.plugin.Config.get("loop_videos", False)))
        self.chkLoop.toggled.connect(self._on_loop_toggled)

        self.chkMute = QCheckBox("Silenciar audio del video", self)
        self.chkMute.setChecked(bool(self.plugin.Config.get("mute_video", False)))
        self.chkMute.toggled.connect(self._on_mute_toggled)

        opts.addWidget(self.chkLoop)
        opts.addWidget(self.chkMute)
        opts.addWidget(QLabel("Vol:", self))

        self.volSlider = QSlider(Qt.Horizontal, self)
        self.volSlider.setRange(0, 100)
        self.volSlider.setValue(int(self.plugin.Config.get("volume", 100)))
        self.volSlider.setFixedWidth(90)
        self.volSlider.valueChanged.connect(self._on_vol_changed)
        opts.addWidget(self.volSlider)

        btn_set_logo = QPushButton("Elegir Logo/Flyer...", self)
        btn_set_logo.clicked.connect(self._choose_standby_image)
        opts.addWidget(btn_set_logo)
        lay.addLayout(opts)

        self.chkAutoCueVideo = QCheckBox(
            "Abrir video automáticamente al reproducir .mp4/.mkv desde la lista manual", self
        )
        self.chkAutoCueVideo.setChecked(bool(self.plugin.Config.get("auto_play_cue_videos", True)))
        self.chkAutoCueVideo.toggled.connect(self._on_auto_cue_video_toggled)
        lay.addWidget(self.chkAutoCueVideo)

        # Video Playlist
        self.listWidget = QListWidget(self)
        self.listWidget.itemDoubleClicked.connect(lambda *_: self.play_selected())
        lay.addWidget(self.listWidget, 1)

        for vpath in self.plugin.Config.get("videos", []):
            if os.path.exists(vpath):
                self._append_item(vpath)

        # Playlist Management Row
        pl_btns = QHBoxLayout()
        btn_add = QPushButton("➕ Agregar Videos (.mp4 / .mkv / .webm)...", self)
        btn_add.clicked.connect(self._add_videos)
        btn_rem = QPushButton("Quitar seleccionado", self)
        btn_rem.clicked.connect(self._remove_selected)
        pl_btns.addWidget(btn_add, 1)
        pl_btns.addWidget(btn_rem)
        lay.addLayout(pl_btns)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_mpv)
        self._timer.start(500)

    def _append_item(self, path):
        it = QListWidgetItem(os.path.basename(path))
        it.setData(Qt.UserRole, path)
        it.setToolTip(path)
        self.listWidget.addItem(it)

    def add_video_file(self, path):
        if not path or not os.path.exists(path):
            return
        self._append_item(path)
        self._save_list()

    def _save_list(self):
        vids = [self.listWidget.item(i).data(Qt.UserRole) for i in range(self.listWidget.count())]
        self.plugin.Config["videos"] = vids
        self.plugin.Config.write()

    def _add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Seleccionar videos para Escenario",
            get_music_dir(),
            "Videos (*.mp4 *.mkv *.webm *.mov *.avi *.m4v);;Todos (*)",
        )
        for f in files:
            self._append_item(f)
        if files:
            self._save_list()

    def _remove_selected(self):
        row = self.listWidget.currentRow()
        if row >= 0:
            self.listWidget.takeItem(row)
            self._save_list()

    def _choose_standby_image(self):
        fname, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Logo o Flyer de Espera",
            get_music_dir(),
            "Imágenes (*.png *.jpg *.jpeg *.webp);;Todos (*)",
        )
        if fname:
            self.plugin.Config["standby_image"] = fname
            self.plugin.Config.write()
            self.show_standby_image()

    def _ipc(self, *args):
        return mpv_ipc_send(IPC_SOCK, *args)

    def _ensure_stage_open(self):
        if self._mpv_proc is not None and self._mpv_proc.poll() is None:
            return True
        if not IS_WINDOWS and os.path.exists(IPC_SOCK):
            try:
                os.unlink(IPC_SOCK)
            except OSError:
                pass
        cmd = [
            "mpv",
            "--idle=yes",
            "--force-window=yes",
            "--keep-open=yes",
            "--osd-level=0",
            "--background-color=#000000",
            "--title=Pantalla de Escenario — DJNova",
            f"--input-ipc-server={IPC_SOCK}",
            f"--volume={self.volSlider.value()}",
            f"--mute={'yes' if self.chkMute.isChecked() else 'no'}",
            f"--loop-file={'inf' if self.chkLoop.isChecked() else 'no'}",
        ]
        try:
            self._mpv_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(25):
                if mpv_ipc_ready(IPC_SOCK):
                    break
                time.sleep(0.08)
            self.btnOpenStage.blockSignals(True)
            self.btnOpenStage.setChecked(True)
            self.btnOpenStage.setText("🖥 Cerrar Pantalla de Escenario")
            self.btnOpenStage.blockSignals(False)
            self.stageLed.setText("● PANTALLA ACTIVA")
            self.stageLed.setStyleSheet(
                "background:#0D281D; border:1px solid #00E676; border-radius:5px; "
                "color:#00E676; font-weight:bold; padding:5px 10px; font-family:monospace;"
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Error abriendo mpv", str(exc))
            return False

    def _toggle_stage_window(self, checked):
        if checked:
            if self._ensure_stage_open():
                standby = self.plugin.Config.get("standby_image", "")
                if standby and os.path.exists(standby):
                    self.show_standby_image()
        else:
            self._close_stage()

    def _close_stage(self):
        if self._mpv_proc is not None:
            try:
                self._ipc("quit")
                self._mpv_proc.wait(timeout=2)
            except Exception:
                try:
                    self._mpv_proc.terminate()
                except Exception:
                    pass
            self._mpv_proc = None
        if not IS_WINDOWS and os.path.exists(IPC_SOCK):
            try:
                os.unlink(IPC_SOCK)
            except OSError:
                pass
        self.btnOpenStage.blockSignals(True)
        self.btnOpenStage.setChecked(False)
        self.btnOpenStage.setText("🖥 Abrir Pantalla de Escenario")
        self.btnOpenStage.blockSignals(False)
        self.stageLed.setText("● PANTALLA CERRADA")
        self.stageLed.setStyleSheet(
            "background:#141821; border:1px solid #36455E; border-radius:5px; "
            "color:#8A9BB5; font-weight:bold; padding:5px 10px; font-family:monospace;"
        )

    def _toggle_fullscreen(self):
        if self._ensure_stage_open():
            self._ipc("cycle", "fullscreen")

    def play_selected(self):
        it = self.listWidget.currentItem()
        if it is None:
            if self.listWidget.count() > 0:
                self.listWidget.setCurrentRow(0)
                it = self.listWidget.currentItem()
            else:
                self._add_videos()
                return
        path = it.data(Qt.UserRole)
        if not path or not os.path.exists(path):
            return
        if not self._ensure_stage_open():
            return
        self.btnBlackout.blockSignals(True)
        self.btnBlackout.setChecked(False)
        self.btnBlackout.blockSignals(False)
        self._blackout = False
        self._active_file = path
        self._ipc("set_property", "vid", "auto")
        self._ipc("set_property", "pause", False)
        self._ipc("set_property", "loop-file", "inf" if self.chkLoop.isChecked() else "no")
        self._ipc("loadfile", path, "replace")
        self.lblCurrent.setText(f"En pantalla: ▶ {os.path.basename(path)}")

    def toggle_pause(self):
        if self._mpv_proc and self._mpv_proc.poll() is None:
            self._ipc("cycle", "pause")

    def stop_video(self):
        self._active_file = ""
        if self._mpv_proc and self._mpv_proc.poll() is None:
            standby = self.plugin.Config.get("standby_image", "")
            if standby and os.path.exists(standby) and not self._blackout:
                self.show_standby_image()
            else:
                self._ipc("stop")
                self.lblCurrent.setText("En pantalla: (Negro / Detenido)")
                self.progressBar.setValue(0)
                self.lblTime.setText("00:00 / 00:00")

    def _toggle_blackout(self, checked):
        self._blackout = checked
        if self._mpv_proc and self._mpv_proc.poll() is None:
            if checked:
                self._ipc("set_property", "vid", "no")
                self.lblCurrent.setText("En pantalla: ⬛ BLACKOUT (Negro)")
            else:
                self._ipc("set_property", "vid", "auto")
                if self._active_file:
                    self.lblCurrent.setText(f"En pantalla: ▶ {os.path.basename(self._active_file)}")

    def show_standby_image(self):
        img = self.plugin.Config.get("standby_image", "")
        if not img or not os.path.exists(img):
            self._choose_standby_image()
            return
        if self._ensure_stage_open():
            self._blackout = False
            self.btnBlackout.setChecked(False)
            self._active_file = img
            self._ipc("set_property", "vid", "auto")
            self._ipc("loadfile", img, "replace")
            self.lblCurrent.setText(f"En pantalla: 🖼 Logo ({os.path.basename(img)})")

    def _on_loop_toggled(self, checked):
        self.plugin.Config["loop_videos"] = bool(checked)
        self.plugin.Config.write()
        self._ipc("set_property", "loop-file", "inf" if checked else "no")

    def _on_mute_toggled(self, checked):
        self.plugin.Config["mute_video"] = bool(checked)
        self.plugin.Config.write()
        self._ipc("set_property", "mute", "yes" if checked else "no")

    def _on_vol_changed(self, val):
        self.plugin.Config["volume"] = int(val)
        self.plugin.Config.write()
        self._ipc("set_property", "volume", int(val))

    def _on_auto_cue_video_toggled(self, checked):
        self.plugin.Config["auto_play_cue_videos"] = bool(checked)
        self.plugin.Config.write()

    def sync_play_cue_video(self, cue, path):
        """Abre la pantalla mpv en silencio (audio sigue en GStreamer) sincronizada con el Cue."""
        was_closed = self._mpv_proc is None or self._mpv_proc.poll() is not None
        if not self._ensure_stage_open():
            return
        if was_closed:
            self._auto_opened = True

        # Si el mismo video estaba en pausa, solo reanudar sin recargar desde 00:00
        if not was_closed and self._synced_cue is cue and self._active_file == path:
            self._ipc("set_property", "mute", "yes")
            self._ipc("set_property", "pause", False)
            try:
                cur_ms = cue.current_time() or 0
                if cur_ms > 200:
                    self._ipc("seek", cur_ms / 1000.0, "absolute")
            except Exception:
                pass
            return

        self._synced_cue = cue
        self._active_file = path
        self.btnBlackout.blockSignals(True)
        self.btnBlackout.setChecked(False)
        self.btnBlackout.blockSignals(False)
        self._blackout = False

        # Silenciar mpv para evitar doble audio (DJNova reproduce el audio por EQ/Master)
        self._ipc("set_property", "mute", "yes")
        self._ipc("set_property", "vid", "auto")
        self._ipc("set_property", "pause", False)
        self._ipc("set_property", "loop-file", "no")
        self._ipc("loadfile", path, "replace")
        self.lblCurrent.setText(f"Video de Lista (Cue): ▶ {os.path.basename(path)}")

        def _sync_initial_pos(c=cue):
            if self._synced_cue is not c:
                return
            try:
                cur_ms = c.current_time() or 0
                if cur_ms > 350:
                    self._ipc("seek", cur_ms / 1000.0, "absolute")
            except Exception:
                pass

        QTimer.singleShot(250, _sync_initial_pos)

    def sync_pause_cue(self, cue):
        if self._synced_cue is cue and self._mpv_proc and self._mpv_proc.poll() is None:
            self._ipc("set_property", "pause", True)

    def sync_stop_cue(self, cue):
        if self._synced_cue is not cue:
            return
        self._synced_cue = None
        # Restaurar preferencia de mute del usuario para videos manuales del Escenario
        self._ipc("set_property", "mute", "yes" if self.chkMute.isChecked() else "no")
        standby = self.plugin.Config.get("standby_image", "")
        if standby and os.path.exists(standby) and not self._blackout:
            self.show_standby_image()
            return

        def _maybe_close():
            if self._synced_cue is None and self._auto_opened:
                self._auto_opened = False
                self._close_stage()
            elif self._synced_cue is None:
                self.stop_video()

        QTimer.singleShot(150, _maybe_close)

    def _poll_mpv(self):
        if self._mpv_proc is not None and self._mpv_proc.poll() is not None:
            self._synced_cue = None
            self._auto_opened = False
            self._close_stage()
            return
        if self._mpv_proc is None:
            return
        pos_r = self._ipc("get_property", "time-pos")
        dur_r = self._ipc("get_property", "duration")
        pos = pos_r.get("data") if isinstance(pos_r, dict) else None
        dur = dur_r.get("data") if isinstance(dur_r, dict) else None
        if isinstance(pos, (int, float)) and isinstance(dur, (int, float)) and dur > 0:
            self.lblTime.setText(f"{fmt_sec(pos)} / {fmt_sec(dur)}")
            self.progressBar.setValue(int(min(1000, max(0, (pos / dur) * 1000))))

        # Mantener sincronía si el usuario adelanta/retrocede la pista en la lista
        if (
            self._synced_cue is not None
            and (self._synced_cue.state & CueState.Running)
            and isinstance(pos, (int, float))
        ):
            try:
                cue_sec = (self._synced_cue.current_time() or 0) / 1000.0
                if abs(pos - cue_sec) > 0.75:
                    self._ipc("seek", cue_sec, "absolute")
            except Exception:
                pass

    def get_active_video_path(self):
        if self._active_file and os.path.exists(self._active_file):
            return self._active_file
        it = self.listWidget.currentItem()
        if it:
            return it.data(Qt.UserRole) or ""
        return ""

    def shutdown(self):
        self._timer.stop()
        self._synced_cue = None
        self._close_stage()


class DjVideoStage(Plugin):
    """Reproductor de Videos y Pantalla de Escenario / Proyector (mpv IPC)."""

    Name = "Video y Pantalla de Escenario (Proyector)"
    Authors = ("DJNova",)
    Description = (
        "Reproduce videos (.mp4, .mkv, .webm) y visuales DJ en bucle hacia una ventana "
        "independiente para proyector o segundo monitor, y abre automáticamente el video "
        "cuando reproduces un archivo .mp4 desde tu lista manual."
    )
    Version = "1.1.0"

    def __init__(self, app):
        super().__init__(app)
        self.dialog = None
        self._hooked_cues = set()
        self._bridge = VideoCueBridge()
        self._bridge.cue_started.connect(self._on_cue_started_ui)
        self._bridge.cue_paused.connect(self._on_cue_paused_ui)
        self._bridge.cue_stopped.connect(self._on_cue_stopped_ui)

        self.action = QAction("Video y Pantalla de Escenario... (Ctrl+Shift+V)", self.app.window.menuTools)
        self.action.triggered.connect(self.open_dialog)
        self.app.window.menuTools.addAction(self.action)

        self._shortcut = QShortcut(QKeySequence("Ctrl+Shift+V"), self.app.window)
        self._shortcut.activated.connect(self.open_dialog)

        if hasattr(self.app, "cue_model") and self.app.cue_model is not None:
            self.app.cue_model.item_added.connect(self._hook_cue)
            self.app.cue_model.item_removed.connect(self._unhook_cue)
            for c in list(self.app.cue_model):
                self._hook_cue(c)

    def _emit_cue_started(self, cue):
        self._bridge.cue_started.emit(cue)

    def _emit_cue_paused(self, cue):
        self._bridge.cue_paused.emit(cue)

    def _emit_cue_stopped(self, cue):
        self._bridge.cue_stopped.emit(cue)

    def _hook_cue(self, cue):
        cid = getattr(cue, "id", None)
        if not isinstance(cue, MediaCue) or cid in self._hooked_cues:
            return
        self._hooked_cues.add(cid)
        try:
            cue.started.connect(self._emit_cue_started)
            cue.paused.connect(self._emit_cue_paused)
            cue.stopped.connect(self._emit_cue_stopped)
            cue.end.connect(self._emit_cue_stopped)
            cue.interrupted.connect(self._emit_cue_stopped)
        except Exception:
            logger.exception("Error conectando señales de video al cue")

    def _unhook_cue(self, cue):
        self._hooked_cues.discard(getattr(cue, "id", None))

    def _on_cue_started_ui(self, cue):
        if not bool(self.Config.get("auto_play_cue_videos", True)):
            return
        vpath = get_cue_video_path(cue)
        if not vpath:
            return
        if self.dialog is None:
            self.dialog = VideoStageDialog(self, self.app.window)
        self.dialog.sync_play_cue_video(cue, vpath)

    def _on_cue_paused_ui(self, cue):
        if self.dialog is not None:
            self.dialog.sync_pause_cue(cue)

    def _on_cue_stopped_ui(self, cue):
        if self.dialog is not None:
            self.dialog.sync_stop_cue(cue)

    def open_dialog(self):
        if self.dialog is None:
            self.dialog = VideoStageDialog(self, self.app.window)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def add_video_file(self, path):
        if self.dialog is None:
            self.dialog = VideoStageDialog(self, self.app.window)
        self.dialog.add_video_file(path)

    def finalize(self):
        if self._shortcut is not None:
            self._shortcut.setEnabled(False)
        if self.dialog is not None:
            self.dialog.shutdown()
            self.dialog.close()
        if self.action is not None:
            self.app.window.menuTools.removeAction(self.action)
        super().finalize()
