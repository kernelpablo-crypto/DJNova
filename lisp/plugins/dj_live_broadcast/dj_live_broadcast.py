# -*- coding: utf-8 -*-
import glob
import logging
import os
import re
import subprocess
import time

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QShortcut,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lisp import plugins
from lisp.core.djnova_platform import (
    escape_ffmpeg_drawtext_path,
    get_audio_loopback_input_args,
    get_available_h264_encoders,
    get_camera_input_args,
    get_music_dir,
    get_private_runtime_dir,
    list_video_capture_devices,
    mask_secret,
    validate_rtmp_url,
)
from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState
from lisp.cues.media_cue import MediaCue

logger = logging.getLogger(__name__)

NOW_PLAYING_FILE = os.path.join(get_private_runtime_dir(), "live_now_playing.txt")
STAT_RE = re.compile(r"fps=\s*([\d.]+).*?bitrate=\s*([^\s]+).*?speed=\s*([^\s]+)")


def detect_cameras():
    return list_video_capture_devices()


class CameraPreviewWorker(QThread):
    """Lightweight live webcam preview reader (MJPEG pipe -> QPixmap)."""

    frame_ready = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, device):
        super().__init__()
        self.device = device
        self._proc = None
        self._stop = False

    def stop(self):
        self._stop = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        cmd = [
            "ffmpeg", "-v", "error",
            *get_camera_input_args(self.device, resolution="640x360", fps=15),
            "-vf", "fps=12",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "6",
            "pipe:1",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            buf = bytearray()
            while not self._stop:
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    soi = buf.find(b"\xff\xd8")
                    if soi == -1:
                        buf.clear()
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi == -1:
                        if soi > 0:
                            del buf[:soi]
                        break
                    frame = bytes(buf[soi : eoi + 2])
                    del buf[: eoi + 2]
                    self.frame_ready.emit(frame)
            if not self._stop:
                err = self._proc.stderr.read().decode("utf-8", "replace").strip()
                if err:
                    self.failed.emit(err)
        except Exception as exc:
            if not self._stop:
                self.failed.emit(str(exc))
        finally:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass


class BroadcastStatsWorker(QThread):
    """Reads ffmpeg stderr in background to report live FPS / bitrate / errors."""

    stat_update = pyqtSignal(str)
    log_line = pyqtSignal(str)
    ended = pyqtSignal(int)

    def __init__(self, proc, secrets_to_mask=()):
        super().__init__()
        self.proc = proc
        self.secrets_to_mask = tuple(s for s in secrets_to_mask if s)

    def run(self):
        try:
            for line in self.proc.stderr:
                s = line.strip()
                if not s:
                    continue
                for sec in self.secrets_to_mask:
                    s = mask_secret(s, sec)
                m = STAT_RE.search(s)
                if m:
                    self.stat_update.emit(f"FPS: {m.group(1)} | Bitrate: {m.group(2)} | Vel: {m.group(3)}")
                elif any(k in s.lower() for k in ("error", "failed", "refused", "invalid", "broken", "rtmp", "connection")):
                    self.log_line.emit(s)
        except Exception:
            pass
        rc = self.proc.wait()
        self.ended.emit(rc)


class LiveBroadcastDialog(QDialog):
    """All-in-one Camera + Scenes + YouTube Live / Facebook Live / Multistream console."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.app = plugin.app
        self.setWindowTitle("Transmisión en Vivo — Cámara, Escenas, YouTube & Facebook Live")
        self.setMinimumWidth(720)
        self.setMinimumHeight(560)

        self._preview_worker = None
        self._stream_proc = None
        self._stats_worker = None
        self._is_live = False
        self._start_ts = 0.0
        self._last_np = ""

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Top On-Air Status Banner
        top = QHBoxLayout()
        self.liveLed = QLabel("● FUERA DEL AIRE", self)
        self.liveLed.setStyleSheet(
            "background:#141821; border:1px solid #36455E; border-radius:5px; "
            "color:#8A9BB5; font-weight:bold; padding:6px 12px; font-family:monospace;"
        )
        self.timerLbl = QLabel("00:00:00", self)
        self.timerLbl.setObjectName("DJConsoleClock")
        self.timerLbl.setStyleSheet("font-size: 13pt; padding: 4px 10px;")
        self.statsLbl = QLabel("Listo para transmitir (Audio Maestro + Video)", self)
        self.statsLbl.setStyleSheet("color:#00D4FF; font-family:monospace; font-size:9pt;")
        top.addWidget(self.liveLed)
        top.addWidget(self.timerLbl)
        top.addWidget(self.statsLbl, 1)
        root.addLayout(top)

        # Main Tabs
        tabs = QTabWidget(self)
        root.addWidget(tabs, 1)

        cfg = self.plugin.Config

        # === TAB 1: CAMERA & SCENES ===
        tab_scene = QWidget(self)
        sc_lay = QHBoxLayout(tab_scene)
        sc_lay.setSpacing(10)

        # Left: Preview Monitor
        mon_col = QVBoxLayout()
        self.previewLabel = QLabel("Vista previa de cámara / escena\n(Pulsa 'Activar Vista Previa')", self)
        self.previewLabel.setAlignment(Qt.AlignCenter)
        self.previewLabel.setFixedSize(360, 210)
        self.previewLabel.setStyleSheet(
            "background:#05070B; border:2px solid #2E3C54; border-radius:6px; color:#7A8CA8;"
        )
        mon_col.addWidget(self.previewLabel)

        prev_btns = QHBoxLayout()
        self.btnPreview = QPushButton("👁 Activar Vista Previa Cámara", self)
        self.btnPreview.setCheckable(True)
        self.btnPreview.toggled.connect(self._toggle_preview)
        btn_refresh_cam = QPushButton("Buscar Cámaras", self)
        btn_refresh_cam.clicked.connect(self._refresh_cameras)
        prev_btns.addWidget(self.btnPreview, 1)
        prev_btns.addWidget(btn_refresh_cam)
        mon_col.addLayout(prev_btns)

        self.logBox = QTextEdit(self)
        self.logBox.setReadOnly(True)
        self.logBox.setMaximumHeight(110)
        self.logBox.setStyleSheet("font-family:monospace; font-size:8.5pt;")
        mon_col.addWidget(self.logBox)
        sc_lay.addLayout(mon_col)

        # Right: Scene & Media Pickers
        ctrl_col = QVBoxLayout()
        form_sc = QFormLayout()
        form_sc.setSpacing(7)

        self.camCombo = QComboBox(self)
        self._refresh_cameras(initial=cfg.get("camera_device", ""))
        form_sc.addRow("Cámara:", self.camCombo)

        self.sceneCombo = QComboBox(self)
        scenes = [
            ("camera", "1. Cámara en Vivo"),
            ("camera_logo", "2. Cámara + Logo PNG superpuesto"),
            ("flyer_nowplaying", "3. Flyer / Portada + 'Ahora Suena' en vivo"),
            ("video_loop", "4. Video en Bucle + Audio de Consola"),
        ]
        saved_scene = cfg.get("scene", "camera")
        s_idx = 0
        for i, (k, lbl) in enumerate(scenes):
            self.sceneCombo.addItem(lbl, k)
            if k == saved_scene:
                s_idx = i
        self.sceneCombo.setCurrentIndex(s_idx)
        form_sc.addRow("Escena:", self.sceneCombo)

        # Logo picker
        logo_row = QHBoxLayout()
        self.logoEdit = QLineEdit(str(cfg.get("logo_path", "")), self)
        self.logoEdit.setPlaceholderText("Logo transparente .png (opcional)")
        btn_logo = QPushButton("...", self)
        btn_logo.setFixedWidth(30)
        btn_logo.clicked.connect(lambda: self._pick_file(self.logoEdit, "Imágenes (*.png *.jpg *.webp)"))
        logo_row.addWidget(self.logoEdit, 1)
        logo_row.addWidget(btn_logo)
        form_sc.addRow("Logo PNG:", logo_row)

        # Flyer picker
        flyer_row = QHBoxLayout()
        self.flyerEdit = QLineEdit(str(cfg.get("flyer_path", "")), self)
        self.flyerEdit.setPlaceholderText("Imagen de fondo para Escena 3 (opcional)")
        btn_flyer = QPushButton("...", self)
        btn_flyer.setFixedWidth(30)
        btn_flyer.clicked.connect(lambda: self._pick_file(self.flyerEdit, "Imágenes (*.png *.jpg *.jpeg *.webp)"))
        flyer_row.addWidget(self.flyerEdit, 1)
        flyer_row.addWidget(btn_flyer)
        form_sc.addRow("Flyer Fondo:", flyer_row)

        # Video picker
        vid_row = QHBoxLayout()
        self.videoEdit = QLineEdit(str(cfg.get("video_path", "")), self)
        self.videoEdit.setPlaceholderText("Video .mp4 para Escena 4 (o toma el del Escenario)")
        btn_vid = QPushButton("...", self)
        btn_vid.setFixedWidth(30)
        btn_vid.clicked.connect(lambda: self._pick_file(self.videoEdit, "Videos (*.mp4 *.mkv *.webm *.mov)"))
        vid_row.addWidget(self.videoEdit, 1)
        vid_row.addWidget(btn_vid)
        form_sc.addRow("Video Loop:", vid_row)

        # Quality / Encoder
        self.resCombo = QComboBox(self)
        for r in ("1280x720", "1920x1080", "854x480"):
            self.resCombo.addItem(r, r)
        idx_r = self.resCombo.findData(cfg.get("resolution", "1280x720"))
        if idx_r >= 0:
            self.resCombo.setCurrentIndex(idx_r)
        form_sc.addRow("Resolución:", self.resCombo)

        self.brCombo = QComboBox(self)
        for b in ("2500k", "3500k", "4500k", "6000k"):
            self.brCombo.addItem(f"{b} bps", b)
        idx_b = self.brCombo.findData(cfg.get("bitrate", "3500k"))
        if idx_b >= 0:
            self.brCombo.setCurrentIndex(idx_b)
        form_sc.addRow("Bitrate Video:", self.brCombo)

        self.encCombo = QComboBox(self)
        for lbl, key in get_available_h264_encoders():
            self.encCombo.addItem(lbl, key)
        idx_e = self.encCombo.findData(cfg.get("encoder", "libx264"))
        if idx_e >= 0:
            self.encCombo.setCurrentIndex(idx_e)
        form_sc.addRow("Codificador:", self.encCombo)

        ctrl_col.addLayout(form_sc)
        ctrl_col.addStretch(1)
        sc_lay.addLayout(ctrl_col, 1)

        tabs.addTab(tab_scene, "📹 Cámara, Escenas y Calidad")

        # === TAB 2: YOUTUBE / FACEBOOK / CUSTOM RTMP ===
        tab_dest = QWidget(self)
        dest_lay = QVBoxLayout(tab_dest)
        dest_lay.setSpacing(10)

        note = QLabel(
            "Puedes activar YouTube Live, Facebook Live o ambos al mismo tiempo (Multistream con 1 sola codificación):",
            self,
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#00D4FF; font-weight:bold;")
        dest_lay.addWidget(note)

        # YouTube Group
        yt_box = QGroupBox("YouTube Live (RTMP)", self)
        yt_form = QFormLayout(yt_box)
        self.chkYt = QCheckBox("Activar transmisión a YouTube Live", self)
        self.chkYt.setChecked(bool(cfg.get("yt_enabled", True)))
        self.ytUrl = QLineEdit(str(cfg.get("yt_url", "rtmp://a.rtmp.youtube.com/live2")), self)
        self.ytKey = QLineEdit(str(cfg.get("yt_key", "")), self)
        self.ytKey.setEchoMode(QLineEdit.Password)
        self.ytKey.setPlaceholderText("Pega aquí tu Clave de transmisión de YouTube Studio")
        yt_form.addRow(self.chkYt)
        yt_form.addRow("URL Servidor:", self.ytUrl)
        yt_form.addRow("Clave Stream:", self._with_eye(self.ytKey))
        dest_lay.addWidget(yt_box)

        # Facebook Group
        fb_box = QGroupBox("Facebook Live (RTMPS Seguro)", self)
        fb_form = QFormLayout(fb_box)
        self.chkFb = QCheckBox("Activar transmisión a Facebook Live", self)
        self.chkFb.setChecked(bool(cfg.get("fb_enabled", False)))
        self.fbUrl = QLineEdit(str(cfg.get("fb_url", "rtmps://live-api-s.facebook.com:443/rtmp")), self)
        self.fbKey = QLineEdit(str(cfg.get("fb_key", "")), self)
        self.fbKey.setEchoMode(QLineEdit.Password)
        self.fbKey.setPlaceholderText("Pega aquí tu Clave de transmisión de Facebook Live Producer")
        fb_form.addRow(self.chkFb)
        fb_form.addRow("URL Servidor:", self.fbUrl)
        fb_form.addRow("Clave Stream:", self._with_eye(self.fbKey))
        dest_lay.addWidget(fb_box)

        # Custom RTMP Group
        cu_box = QGroupBox("RTMP Personalizado (Twitch / Kick / TikTok / Propio)", self)
        cu_form = QFormLayout(cu_box)
        self.chkCustom = QCheckBox("Activar destino RTMP personalizado", self)
        self.chkCustom.setChecked(bool(cfg.get("custom_enabled", False)))
        self.cuUrl = QLineEdit(str(cfg.get("custom_url", "")), self)
        self.cuUrl.setPlaceholderText("rtmp://live.twitch.tv/app")
        self.cuKey = QLineEdit(str(cfg.get("custom_key", "")), self)
        self.cuKey.setEchoMode(QLineEdit.Password)
        cu_form.addRow(self.chkCustom)
        cu_form.addRow("URL Servidor:", self.cuUrl)
        cu_form.addRow("Clave Stream:", self._with_eye(self.cuKey))
        dest_lay.addWidget(cu_box)
        dest_lay.addStretch(1)

        tabs.addTab(tab_dest, "🌐 Destinos (YouTube / Facebook / Multistream)")

        # Bottom Go Live Action Bar
        bot = QHBoxLayout()
        self.btnGoLive = QPushButton("● INICIAR TRANSMISIÓN EN VIVO (YOUTUBE / FACEBOOK)", self)
        self.btnGoLive.setCheckable(True)
        self.btnGoLive.setFixedHeight(42)
        self.btnGoLive.setStyleSheet(
            """
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E51C23, stop:1 #990D14);
                border: 2px solid #FF5252; color: #FFFFFF; font-weight: bold; font-size: 10.5pt;
            }
            QPushButton:hover { background-color: #FF2A4B; }
            QPushButton:checked {
                background-color: #B71C1C; border: 2px solid #FFFFFF; color: #FFFFFF;
            }
            """
        )
        self.btnGoLive.toggled.connect(self._on_live_toggled)
        bot.addWidget(self.btnGoLive, 1)
        root.addLayout(bot)

        # Timer for live clock & updating now-playing text file
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._write_now_playing("Cabina DJ — En Vivo")

    def _with_eye(self, line_edit):
        w = QWidget(self)
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("👁", w)
        btn.setCheckable(True)
        btn.setFixedWidth(30)
        btn.toggled.connect(lambda ch: line_edit.setEchoMode(QLineEdit.Normal if ch else QLineEdit.Password))
        l.addWidget(line_edit, 1)
        l.addWidget(btn)
        return w

    def _pick_file(self, target_edit, flt):
        fname, _ = QFileDialog.getOpenFileName(self, "Seleccionar archivo", get_music_dir(), flt)
        if fname:
            target_edit.setText(fname)

    def _refresh_cameras(self, initial=None):
        cur = initial or self.camCombo.currentText()
        self.camCombo.clear()
        cams = detect_cameras()
        self.camCombo.addItems(cams)
        idx = self.camCombo.findText(cur)
        if idx >= 0:
            self.camCombo.setCurrentIndex(idx)

    def _log(self, msg):
        self.logBox.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _save_cfg(self):
        cfg = self.plugin.Config
        cfg["camera_device"] = self.camCombo.currentText()
        cfg["scene"] = self.sceneCombo.currentData()
        cfg["logo_path"] = self.logoEdit.text().strip()
        cfg["flyer_path"] = self.flyerEdit.text().strip()
        cfg["video_path"] = self.videoEdit.text().strip()
        cfg["resolution"] = self.resCombo.currentData()
        cfg["bitrate"] = self.brCombo.currentData()
        cfg["encoder"] = self.encCombo.currentData()
        cfg["yt_enabled"] = self.chkYt.isChecked()
        cfg["yt_url"] = self.ytUrl.text().strip()
        cfg["yt_key"] = self.ytKey.text().strip()
        cfg["fb_enabled"] = self.chkFb.isChecked()
        cfg["fb_url"] = self.fbUrl.text().strip()
        cfg["fb_key"] = self.fbKey.text().strip()
        cfg["custom_enabled"] = self.chkCustom.isChecked()
        cfg["custom_url"] = self.cuUrl.text().strip()
        cfg["custom_key"] = self.cuKey.text().strip()
        cfg.write()

    def _toggle_preview(self, checked):
        if checked:
            if self._is_live:
                self._log("La cámara ya está en uso por la transmisión en vivo.")
                self.btnPreview.setChecked(False)
                return
            dev = self.camCombo.currentText()
            self._preview_worker = CameraPreviewWorker(dev)
            self._preview_worker.frame_ready.connect(self._show_preview_frame)
            self._preview_worker.failed.connect(self._on_preview_error)
            self._preview_worker.start()
            self.btnPreview.setText("■ Detener Vista Previa")
            self._log(f"Vista previa activada en {dev}.")
        else:
            if self._preview_worker is not None:
                self._preview_worker.stop()
                self._preview_worker.wait(1500)
                self._preview_worker = None
            self.btnPreview.setText("👁 Activar Vista Previa Cámara")
            self.previewLabel.setPixmap(QPixmap())
            self.previewLabel.setText("Vista previa detenida")

    def _show_preview_frame(self, jpg_bytes):
        pm = QPixmap()
        if pm.loadFromData(jpg_bytes, "JPG"):
            self.previewLabel.setPixmap(
                pm.scaled(self.previewLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _on_preview_error(self, err):
        self._log(f"Cámara preview: {err}")
        self.btnPreview.setChecked(False)

    def _collect_targets(self):
        targets = []
        raw_pairs = [
            ("YouTube Live", self.chkYt.isChecked(), self.ytUrl.text().strip(), self.ytKey.text().strip()),
            ("Facebook Live", self.chkFb.isChecked(), self.fbUrl.text().strip(), self.fbKey.text().strip()),
            ("RTMP Custom", self.chkCustom.isChecked(), self.cuUrl.text().strip(), self.cuKey.text().strip()),
        ]
        for label, enabled, base, key in raw_pairs:
            if not (enabled and base and key):
                continue
            full = f"{base.rstrip('/')}/{key}"
            valid = validate_rtmp_url(full)
            if not valid:
                QMessageBox.warning(
                    self,
                    "URL / Clave inválida",
                    f"El destino '{label}' contiene caracteres inválidos o no usa rtmp:// o rtmps://.",
                )
                return []
            targets.append((label, valid, key))
        return targets

    def _write_now_playing(self, text):
        try:
            tmp = NOW_PLAYING_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, NOW_PLAYING_FILE)
        except Exception:
            pass

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

    def _on_live_toggled(self, checked):
        if checked:
            self._start_live()
        else:
            self._stop_live()

    def _start_live(self):
        targets = self._collect_targets()
        if not targets:
            QMessageBox.warning(
                self,
                "Falta Clave de Transmisión",
                "Ve a la pestaña 'Destinos (YouTube / Facebook)' y activa al menos una plataforma "
                "ingresando su Clave de transmisión (Stream Key).",
            )
            self.btnGoLive.setChecked(False)
            return

        self._save_cfg()

        # Stop preview so camera /dev/videoX is completely free
        if self.btnPreview.isChecked():
            self.btnPreview.setChecked(False)
            time.sleep(0.3)

        scene = self.sceneCombo.currentData()
        res = self.resCombo.currentData()
        w_str, h_str = res.split("x")
        bitrate = self.brCombo.currentData()
        enc = self.encCombo.currentData()
        audio_in_args, _ = get_audio_loopback_input_args()

        cmd = ["ffmpeg", "-y"]
        if enc == "h264_vaapi":
            cmd += ["-vaapi_device", "/dev/dri/renderD128"]

        filter_complex = None
        v_map = "0:v"
        a_idx = 1

        if scene in ("camera", "camera_logo"):
            cam = self.camCombo.currentText()
            cam_size = "1280x720" if int(w_str) >= 1280 else "640x360"
            cmd += get_camera_input_args(cam, resolution=cam_size, fps=30)
            logo = self.logoEdit.text().strip()
            if scene == "camera_logo" and logo and os.path.exists(logo):
                cmd += ["-i", logo]
                a_idx = 2
                scale_part = f"[0:v]scale={w_str}:{h_str}:force_original_aspect_ratio=decrease,pad={w_str}:{h_str}:(ow-iw)/2:(oh-ih)/2[bg];[1:v]scale=160:-1[lg];[bg][lg]overlay=W-w-24:24"
                if enc == "h264_vaapi":
                    filter_complex = scale_part + ",format=nv12,hwupload[vout]"
                else:
                    filter_complex = scale_part + ",format=yuv420p[vout]"
                v_map = "[vout]"
            else:
                if enc == "h264_vaapi":
                    filter_complex = f"[0:v]scale={w_str}:{h_str},format=nv12,hwupload[vout]"
                else:
                    filter_complex = f"[0:v]scale={w_str}:{h_str},format=yuv420p[vout]"
                v_map = "[vout]"

        elif scene == "flyer_nowplaying":
            flyer = self.flyerEdit.text().strip()
            if flyer and os.path.exists(flyer):
                cmd += ["-loop", "1", "-framerate", "30", "-i", flyer]
            else:
                cmd += ["-f", "lavfi", "-i", f"color=c=#0B0E14:s={res}:r=30"]
            cur = self._current_running_title() or "Cabina DJ — En Vivo"
            self._write_now_playing(f"AHORA SUENA: {cur}")
            np_escaped = escape_ffmpeg_drawtext_path(NOW_PLAYING_FILE)
            draw = (
                f"drawbox=x=0:y=ih-72:w=iw:h=72:color=black@0.72:t=fill,"
                f"drawtext=textfile='{np_escaped}':reload=1:fontcolor=#00D4FF:"
                f"fontsize=28:x=28:y=h-48"
            )
            base_f = f"[0:v]scale={w_str}:{h_str}:force_original_aspect_ratio=decrease,pad={w_str}:{h_str}:(ow-iw)/2:(oh-ih)/2,{draw}"
            if enc == "h264_vaapi":
                filter_complex = base_f + ",format=nv12,hwupload[vout]"
            else:
                filter_complex = base_f + ",format=yuv420p[vout]"
            v_map = "[vout]"

        elif scene == "video_loop":
            vpath = self.videoEdit.text().strip()
            if not vpath or not os.path.exists(vpath):
                try:
                    vs = plugins.get_plugin("DjVideoStage")
                    if vs and vs.dialog:
                        vpath = vs.dialog.get_active_video_path()
                except Exception:
                    pass
            if not vpath or not os.path.exists(vpath):
                QMessageBox.warning(
                    self, "Sin video seleccionado", "Selecciona un archivo de video .mp4 en 'Video Loop' para la Escena 4."
                )
                self.btnGoLive.setChecked(False)
                return
            cmd += ["-re", "-stream_loop", "-1", "-i", vpath]
            base_f = f"[0:v]scale={w_str}:{h_str}:force_original_aspect_ratio=decrease,pad={w_str}:{h_str}:(ow-iw)/2:(oh-ih)/2"
            if enc == "h264_vaapi":
                filter_complex = base_f + ",format=nv12,hwupload[vout]"
            else:
                filter_complex = base_f + ",format=yuv420p[vout]"
            v_map = "[vout]"

        # Audio input: master monitor (PipeWire/PulseAudio en Linux, DirectShow en Windows)
        cmd += audio_in_args

        if filter_complex:
            cmd += ["-filter_complex", filter_complex]

        cmd += ["-map", v_map, "-map", f"{a_idx}:a"]

        # Video codec settings (YouTube & Facebook Live compliant: 2s GOP = 60 frames @ 30fps)
        if enc == "h264_vaapi":
            cmd += ["-c:v", "h264_vaapi", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate, "-g", "60", "-r", "30"]
        elif enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
            cmd += ["-c:v", enc, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate, "-g", "60", "-r", "30"]
        else:
            cmd += [
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
                "-g", "60", "-r", "30",
            ]

        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]

        if len(targets) == 1:
            cmd += ["-f", "flv", targets[0][1]]
        else:
            # Multistream tee muxer (single encode -> YouTube + Facebook simultaneously!)
            cmd += ["-flags", "+global_header", "-f", "tee"]
            tee_spec = "|".join(f"[f=flv:onfail=ignore]{url}" for _, url, _ in targets)
            cmd.append(tee_spec)

        secrets_list = [k for _, _, k in targets]
        try:
            self._stream_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._is_live = True
            self._start_ts = time.monotonic()
            names = " + ".join(n for n, _, _ in targets)
            self.btnGoLive.setText(f"■ DETENER TRANSMISIÓN ({names})")
            self.liveLed.setText(f"● EN VIVO: {names}")
            self.liveLed.setStyleSheet(
                "background:#330A10; border:2px solid #FF2A4B; border-radius:5px; "
                "color:#FF5252; font-weight:bold; padding:6px 12px; font-family:monospace;"
            )
            self.previewLabel.setText(f"● TRANSMITIENDO EN VIVO\n{names}\nEscena: {self.sceneCombo.currentText()}")
            self._log(f"Iniciando transmisión hacia {names} ({res} @ {bitrate}, {enc})...")

            self._stats_worker = BroadcastStatsWorker(self._stream_proc, secrets_list)
            self._stats_worker.stat_update.connect(self.statsLbl.setText)
            self._stats_worker.log_line.connect(self._log)
            self._stats_worker.ended.connect(self._on_stream_ended)
            self._stats_worker.start()
        except Exception as exc:
            self._is_live = False
            self.btnGoLive.setChecked(False)
            msg = str(exc)
            for sec in secrets_list:
                msg = mask_secret(msg, sec)
            self._log(f"ERROR iniciando transmisión: {msg}")

    def _stop_live(self):
        self._is_live = False
        if self._stream_proc is not None:
            try:
                self._stream_proc.terminate()
                self._stream_proc.wait(timeout=3)
            except Exception:
                try:
                    self._stream_proc.kill()
                except Exception:
                    pass
            self._stream_proc = None
        self.btnGoLive.setText("● INICIAR TRANSMISIÓN EN VIVO (YOUTUBE / FACEBOOK)")
        self.liveLed.setText("● FUERA DEL AIRE")
        self.liveLed.setStyleSheet(
            "background:#141821; border:1px solid #36455E; border-radius:5px; "
            "color:#8A9BB5; font-weight:bold; padding:6px 12px; font-family:monospace;"
        )
        self.timerLbl.setText("00:00:00")
        self.statsLbl.setText("Transmisión detenida.")
        self.previewLabel.setText("Vista previa de cámara / escena\n(Pulsa 'Activar Vista Previa')")

    def _on_stream_ended(self, rc):
        if self._is_live:
            self._log(f"La transmisión finalizó (código ffmpeg {rc}). Verifica tu clave RTMP o conexión.")
            self.btnGoLive.blockSignals(True)
            self.btnGoLive.setChecked(False)
            self.btnGoLive.blockSignals(False)
            self._stop_live()

    def _tick(self):
        cur = self._current_running_title()
        if cur and cur != self._last_np:
            self._last_np = cur
            self._write_now_playing(f"AHORA SUENA: {cur}")

        if self._is_live:
            secs = max(0, int(time.monotonic() - self._start_ts))
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            self.timerLbl.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def shutdown(self):
        self._timer.stop()
        if self.btnPreview.isChecked():
            self._toggle_preview(False)
        self._stop_live()


class DjLiveBroadcast(Plugin):
    """Cámara en Vivo, 4 Escenas y Transmisión RTMP/RTMPS a YouTube Live y Facebook Live."""

    Name = "Transmisión en Vivo (Cámara / YouTube / Facebook)"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Conecta tu cámara, elige entre 4 escenas (Cámara, Cámara+Logo, Flyer+Ahora Suena, Video) "
        "y transmite en vivo por YouTube Live, Facebook Live o ambos en Multistream con tu audio maestro."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        self.dialog = None
        self.action = QAction(
            "Transmisión en Vivo (Cámara / YouTube / Facebook)... (Ctrl+Shift+L)",
            self.app.window.menuTools,
        )
        self.action.triggered.connect(self.open_dialog)
        self.app.window.menuTools.addAction(self.action)

        self._shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self.app.window)
        self._shortcut.activated.connect(self.open_dialog)

    def open_dialog(self):
        if self.dialog is None:
            self.dialog = LiveBroadcastDialog(self, self.app.window)
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
