# -*- coding: utf-8 -*-
import datetime
import logging
import math
import os
import subprocess
import time
from collections import deque

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, QTime
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAction,
    QApplication,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QSlider,
    QTextEdit,
)

from lisp.core.djnova_platform import (
    IS_WINDOWS,
    get_audio_loopback_input_args,
    get_music_dir,
    open_folder,
)
from lisp.core.fade_functions import FadeOutType
from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState
from lisp.cues.media_cue import MediaCue
from lisp.ui import themes
from lisp.ui.icons import IconTheme

logger = logging.getLogger(__name__)

RECORDINGS_DIR = get_music_dir("Grabaciones_DJNova")


class DimKeyFilter(QObject):
    """Smart event filter: toggles DIM (-12 dB) on key 0 (top row & Numpad) when not typing."""

    def __init__(self, console_widget, main_window):
        super().__init__(main_window)
        self.console = console_widget
        self.main_window = main_window

    def _is_typing_context(self):
        if QApplication.activeModalWidget() is not None:
            return True
        active_win = QApplication.activeWindow()
        if active_win is not None and active_win is not self.main_window:
            return True
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        if isinstance(fw, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
            return True
        if isinstance(fw, QComboBox) and fw.isEditable():
            return True
        return False

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
            mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
            if mods:
                return False
            if event.key() == Qt.Key_0:
                if self._is_typing_context():
                    return False
                self.console.btnDim.toggle()
                return True
        return False


class DJConsoleWidget(QFrame):
    """Hardware-styled DJ & Broadcast Audio Console control strip."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.app = plugin.app
        self.setObjectName("DJConsolePanel")

        self._tap_times = deque(maxlen=8)
        self._master_gain = 1.0
        self._dim_active = False
        self._mute_active = False

        # Live Mix Recorder state
        self._is_recording = False
        self._rec_proc = None
        self._rec_start_ts = 0.0
        self._rec_last_file = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(4)

        # 1. Digital Booth Clock + Live REC Button
        self.clockLabel = QLabel("00:00:00", self)
        self.clockLabel.setObjectName("DJConsoleClock")
        self.clockLabel.setAlignment(Qt.AlignCenter)
        self.clockLabel.setStyleSheet("font-size: 12pt; padding: 3px 5px;")
        self.clockLabel.setFixedWidth(84)
        self.clockLabel.setToolTip("Reloj digital de cabina")
        layout.addWidget(self.clockLabel)

        self.btnRec = QPushButton("● REC", self)
        self.btnRec.setCheckable(True)
        self.btnRec.setFixedWidth(64)
        self.btnRec.setToolTip(
            "Grabar la mezcla/sesión en vivo a MP3 320kbps en ~/Música/Grabaciones_LiSP (Ctrl+R)"
        )
        self.btnRec.setStyleSheet(
            """
            QPushButton {
                background-color: #1A1216; border: 1px solid #7A2230; border-radius: 4px;
                color: #FF6B81; font-weight: bold; font-size: 8.5pt; padding: 4px 5px;
            }
            QPushButton:hover { border: 1px solid #FF2A4B; color: #FFFFFF; }
            QPushButton:checked {
                background-color: #D91E36; border: 1px solid #FFFFFF; color: #FFFFFF;
            }
            """
        )
        self.btnRec.toggled.connect(self._on_rec_toggled)
        layout.addWidget(self.btnRec)

        layout.addWidget(self._vsep())

        # 2. Transport / Console Master Buttons
        self.btnFadeAll = QPushButton(IconTheme.get("fadeout-generic"), "FADE", self)
        self.btnFadeAll.setObjectName("DJBtnFadeAll")
        self.btnFadeAll.setToolTip("Fundido de salida (Fade-Out) suave a todos los cues (Esc)")
        self.btnFadeAll.clicked.connect(self._on_fade_all)
        layout.addWidget(self.btnFadeAll)

        self.btnPauseAll = QPushButton(IconTheme.get("cue-pause"), "PAUSA", self)
        self.btnPauseAll.setToolTip("Pausar todos los cues activos")
        self.btnPauseAll.clicked.connect(self._on_pause_all)
        layout.addWidget(self.btnPauseAll)

        self.btnResumeAll = QPushButton(IconTheme.get("cue-start"), "PLAY", self)
        self.btnResumeAll.setToolTip("Reanudar todos los cues pausados")
        self.btnResumeAll.clicked.connect(self._on_resume_all)
        layout.addWidget(self.btnResumeAll)

        self.btnPanic = QPushButton(IconTheme.get("cue-stop"), "STOP", self)
        self.btnPanic.setObjectName("DJBtnPanic")
        self.btnPanic.setToolTip("Detener inmediatamente todos los cues (Ctrl+Shift+S)")
        self.btnPanic.clicked.connect(self._on_stop_all)
        layout.addWidget(self.btnPanic)

        layout.addWidget(self._vsep())

        # 3. Crossfade Section
        self.cfSpin = QDoubleSpinBox(self)
        self.cfSpin.setDecimals(1)
        self.cfSpin.setRange(0.5, 15.0)
        self.cfSpin.setSingleStep(0.5)
        self.cfSpin.setSuffix("s")
        self.cfSpin.setValue(float(self.plugin.Config.get("crossfade_sec", 3.0)))
        self.cfSpin.setFixedWidth(62)
        self.cfSpin.setToolTip("Duración del Crossfade en segundos")
        self.cfSpin.valueChanged.connect(self._on_cf_changed)
        layout.addWidget(self.cfSpin)

        self.btnCrossfade = QPushButton("XFADE ▶", self)
        self.btnCrossfade.setObjectName("DJBtnCrossfade")
        self.btnCrossfade.setToolTip(
            "Mezcla suave: fade-out a las pistas sonando y lanza con fade-in la siguiente/seleccionada (Ctrl+X)"
        )
        self.btnCrossfade.clicked.connect(self._on_crossfade)
        layout.addWidget(self.btnCrossfade)

        layout.addWidget(self._vsep())

        # 4. Master Volume / DIM / MUTE Bus
        masterLbl = QLabel("MSTR:", self)
        masterLbl.setStyleSheet("font-weight: bold; font-size: 8.5pt;")
        layout.addWidget(masterLbl)

        self.masterSlider = QSlider(Qt.Horizontal, self)
        self.masterSlider.setRange(0, 100)
        self.masterSlider.setValue(100)
        self.masterSlider.setFixedWidth(60)
        self.masterSlider.setToolTip("Control maestro de volumen para pistas activas")
        self.masterSlider.valueChanged.connect(self._on_master_slider)
        layout.addWidget(self.masterSlider)

        self.masterDbLabel = QLabel("0.0dB", self)
        self.masterDbLabel.setFixedWidth(44)
        self.masterDbLabel.setAlignment(Qt.AlignCenter)
        self.masterDbLabel.setStyleSheet("font-family: monospace; font-weight: bold; font-size: 8.5pt;")
        layout.addWidget(self.masterDbLabel)

        self.btnDim = QPushButton("DIM [0]", self)
        self.btnDim.setCheckable(True)
        self.btnDim.setFocusPolicy(Qt.NoFocus)
        self.btnDim.setToolTip("Atenúa el volumen maestro (-12 dB) para hablar por micrófono (Tecla 0 o Ctrl+D)")
        self.btnDim.setStyleSheet(
            """
            QPushButton {
                padding: 4px 6px;
            }
            QPushButton:checked {
                background-color: #FFB300;
                border: 1px solid #FFFFFF;
                color: #05080D;
                font-weight: bold;
            }
            """
        )
        self.btnDim.toggled.connect(self._on_dim_toggled)
        layout.addWidget(self.btnDim)

        self.btnMute = QPushButton(IconTheme.get("audio-volume-high-symbolic"), "MUTE", self)
        self.btnMute.setCheckable(True)
        self.btnMute.setToolTip("Silenciar todas las pistas activas (Ctrl+Shift+M)")
        self.btnMute.toggled.connect(self._on_mute_toggled)
        layout.addWidget(self.btnMute)

        layout.addWidget(self._vsep())

        # 5. Tap Tempo / BPM Counter
        self.btnTap = QPushButton("TAP", self)
        self.btnTap.setObjectName("DJBtnTap")
        self.btnTap.setToolTip("Pulsa al ritmo de la música para medir el BPM")
        self.btnTap.clicked.connect(self._on_tap_bpm)
        layout.addWidget(self.btnTap)

        self.bpmDisplay = QLabel("--- BPM", self)
        self.bpmDisplay.setObjectName("DJConsoleBPMDisplay")
        self.bpmDisplay.setAlignment(Qt.AlignCenter)
        self.bpmDisplay.setStyleSheet("font-size: 10pt; padding: 3px 4px;")
        self.bpmDisplay.setFixedWidth(76)
        layout.addWidget(self.bpmDisplay)

        self.btnBpmReset = QPushButton("C", self)
        self.btnBpmReset.setFixedWidth(24)
        self.btnBpmReset.setToolTip("Reiniciar contador BPM")
        self.btnBpmReset.clicked.connect(self._on_bpm_reset)
        layout.addWidget(self.btnBpmReset)

        layout.addStretch(1)

        # 6. Quick Theme Switcher
        self.themeCombo = QComboBox(self)
        self.themeCombo.setFixedWidth(114)
        self.themeCombo.setToolTip("Cambiar tema visual de la consola en vivo")
        self._populate_themes()
        self.themeCombo.currentTextChanged.connect(self._on_theme_changed)
        layout.addWidget(self.themeCombo)

        # Clock & Master-bus sync timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)
        self._tick()

    def _vsep(self):
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _populate_themes(self):
        self.themeCombo.blockSignals(True)
        self.themeCombo.clear()
        names = sorted(themes.themes_names())
        self.themeCombo.addItems(names)
        current = self.app.conf.get("theme.theme", "Dark")
        idx = self.themeCombo.findText(current)
        if idx >= 0:
            self.themeCombo.setCurrentIndex(idx)
        self.themeCombo.blockSignals(False)

    def _rec_elapsed_str(self):
        if not self._is_recording:
            return "00:00"
        secs = max(0, int(time.monotonic() - self._rec_start_ts))
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _tick(self):
        self.clockLabel.setText(QTime.currentTime().toString("HH:mm:ss"))
        if self._is_recording:
            if self._rec_proc and self._rec_proc.poll() is not None:
                self._is_recording = False
                self._rec_proc = None
                self.btnRec.blockSignals(True)
                self.btnRec.setChecked(False)
                self.btnRec.setText("● REC")
                self.btnRec.blockSignals(False)
            else:
                self.btnRec.setText(f"● {self._rec_elapsed_str()}")
        if self._mute_active or self._dim_active or self._master_gain < 0.999:
            self._apply_master_to_running()

    def _on_rec_toggled(self, checked):
        if checked:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        try:
            os.makedirs(RECORDINGS_DIR, exist_ok=True)
            in_args, _ = get_audio_loopback_input_args()
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_file = os.path.join(RECORDINGS_DIR, f"Show_{ts}.mp3")
            self._rec_proc = subprocess.Popen(
                [
                    "ffmpeg", "-y",
                    *in_args,
                    "-c:a", "libmp3lame", "-b:a", "320k",
                    out_file,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._is_recording = True
            self._rec_start_ts = time.monotonic()
            self._rec_last_file = out_file
            logger.info(f"Grabación iniciada: {out_file}")
        except Exception:
            logger.exception("Error iniciando grabación en vivo")
            self._is_recording = False
            self.btnRec.blockSignals(True)
            self.btnRec.setChecked(False)
            self.btnRec.setText("● REC")
            self.btnRec.blockSignals(False)

    def _stop_recording(self):
        if self._rec_proc is not None:
            try:
                if self._rec_proc.stdin:
                    self._rec_proc.stdin.write(b"q\n")
                    self._rec_proc.stdin.flush()
                self._rec_proc.wait(timeout=4)
            except Exception:
                try:
                    self._rec_proc.terminate()
                except Exception:
                    pass
            self._rec_proc = None
        if self._is_recording:
            logger.info(f"Grabación guardada en: {self._rec_last_file}")
        self._is_recording = False
        self.btnRec.setText("● REC")

    def _effective_gain(self):
        if self._mute_active:
            return 0.0
        gain = self._master_gain
        if self._dim_active:
            gain *= 0.25  # ~ -12 dB
        return gain

    def _safe_layout(self):
        return self.app.layout if self.app.session is not None else None

    def _apply_master_to_running(self):
        layout = self._safe_layout()
        if layout is None:
            return
        gain = self._effective_gain()
        try:
            for cue in layout.cues(MediaCue):
                if cue.state & CueState.Running and not (cue.is_fading_in() or cue.is_fading_out()):
                    vol = cue.media.element("Volume")
                    if vol is not None:
                        target = vol.volume * gain
                        if abs(vol.live_volume - target) > 0.005:
                            vol.live_volume = target
        except Exception:
            pass

    def _on_master_slider(self, value):
        self._master_gain = value / 100.0
        if value == 0:
            self.masterDbLabel.setText("-∞dB")
        else:
            db = 20.0 * math.log10(self._master_gain)
            self.masterDbLabel.setText(f"{db:+.1f}dB")
        self._apply_master_to_running()

    def _on_dim_toggled(self, checked):
        self._dim_active = checked
        self._apply_master_to_running()

    def _on_mute_toggled(self, checked):
        self._mute_active = checked
        self.btnMute.setIcon(
            IconTheme.get("audio-volume-muted-symbolic" if checked else "audio-volume-high-symbolic")
        )
        self._apply_master_to_running()

    def _on_fade_all(self):
        layout = self._safe_layout()
        if layout is not None:
            layout.fadeout_all()

    def _on_pause_all(self):
        layout = self._safe_layout()
        if layout is not None:
            layout.pause_all()

    def _on_resume_all(self):
        layout = self._safe_layout()
        if layout is not None:
            layout.resume_all()

    def _on_stop_all(self):
        layout = self._safe_layout()
        if layout is not None:
            layout.stop_all()

    def _on_cf_changed(self, val):
        self.plugin.Config["crossfade_sec"] = float(val)
        self.plugin.Config.write()

    def _on_crossfade(self):
        layout = self._safe_layout()
        if layout is None:
            return
        duration = float(self.cfSpin.value())

        target_cue = None
        selected = list(layout.selected_cues())
        if selected:
            target_cue = selected[0]
        else:
            try:
                target_cue = layout.standby_cue()
            except Exception:
                target_cue = None

        for cue in layout.cues(MediaCue):
            if cue is target_cue:
                continue
            if cue.state & CueState.Running:
                try:
                    cue.fadeout(duration, FadeOutType.Quadratic)
                except Exception:
                    cue.stop(fade=True)

        if target_cue is not None:
            try:
                if isinstance(target_cue, MediaCue):
                    old_fi = target_cue.fadein_duration
                    target_cue.fadein_duration = duration
                    target_cue.start(fade=True)
                    target_cue.fadein_duration = old_fi
                else:
                    target_cue.execute()
            except Exception:
                logger.exception("Error ejecutando crossfade")

    def _on_tap_bpm(self):
        now = time.monotonic()
        if self._tap_times and (now - self._tap_times[-1]) > 2.5:
            self._tap_times.clear()
        self._tap_times.append(now)
        if len(self._tap_times) >= 2:
            intervals = [
                self._tap_times[i] - self._tap_times[i - 1]
                for i in range(1, len(self._tap_times))
            ]
            avg = sum(intervals) / len(intervals)
            if avg > 0:
                bpm = 60.0 / avg
                self.bpmDisplay.setText(f"{bpm:5.1f} BPM")

    def _on_bpm_reset(self):
        self._tap_times.clear()
        self.bpmDisplay.setText("--- BPM")

    def _on_theme_changed(self, theme_name):
        if not theme_name:
            return
        try:
            qt_app = QApplication.instance()
            themes.get_theme(theme_name).apply(qt_app)
            self.app.conf["theme.theme"] = theme_name
            self.app.conf.write()
            logger.info(f'Consola DJ: tema cambiado a "{theme_name}"')
        except Exception:
            logger.exception("Error cambiando tema desde Consola DJ")

    def shutdown(self):
        self._stop_recording()
        self._timer.stop()


class DjConsole(Plugin):
    """Plugin de barra de Consola DJ / Cabina + Grabador + Atajos para Linux Show Player."""

    Name = "Consola DJ"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Barra de consola estilo cabina DJ con reloj LED, grabador de mezcla MP3 (● REC), "
        "FADE/STOP/Crossfade, Fader Maestro (DIM/MUTE), Tap Tempo BPM, atajos DJ y selector de temas."
    )
    Version = "1.1.0"

    def __init__(self, app):
        super().__init__(app)
        self.dock = None
        self.console_widget = None
        self.menu = None
        self._shortcuts = []
        self._dim_key_filter = None

        self._setup_ui()
        self._setup_shortcuts()

    def _setup_ui(self):
        window = self.app.window

        self.dock = QDockWidget("CONSOLA DJ", window)
        self.dock.setObjectName("DJConsoleDock")
        self.dock.setAllowedAreas(Qt.TopDockWidgetArea | Qt.BottomDockWidgetArea)
        self.dock.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
        )

        self.console_widget = DJConsoleWidget(self, self.dock)
        self.dock.setWidget(self.console_widget)
        window.addDockWidget(Qt.TopDockWidgetArea, self.dock)

        if not self.Config.get("show_dock", True):
            self.dock.hide()

        self.menu = QMenu("Consola DJ", window.menuTools)

        toggle_action = QAction("Mostrar barra de Consola DJ", self.menu)
        toggle_action.setCheckable(True)
        toggle_action.setChecked(self.dock.isVisible())
        toggle_action.toggled.connect(self._toggle_dock)
        self.menu.addAction(toggle_action)

        self.menu.addSeparator()

        rec_folder_act = QAction("Abrir carpeta de grabaciones (Música/Grabaciones_DJNova)", self.menu)
        rec_folder_act.triggered.connect(self._open_recordings_folder)
        self.menu.addAction(rec_folder_act)

        if not IS_WINDOWS:
            qpw_act = QAction("Abrir Matriz de Audio PipeWire/JACK (qpwgraph)", self.menu)
            qpw_act.triggered.connect(self._open_qpwgraph)
            self.menu.addAction(qpw_act)

        keys_act = QAction("Ver atajos de teclado DJ...", self.menu)
        keys_act.triggered.connect(self._show_shortcuts_help)
        self.menu.addAction(keys_act)

        window.menuTools.addMenu(self.menu)

    def _setup_shortcuts(self):
        w = self.app.window
        bindings = [
            ("Escape", self.console_widget._on_fade_all),
            ("Ctrl+Shift+S", self.console_widget._on_stop_all),
            ("Ctrl+X", self.console_widget._on_crossfade),
            ("Ctrl+D", self.console_widget.btnDim.toggle),
            ("Ctrl+Shift+M", self.console_widget.btnMute.toggle),
            ("Ctrl+R", self.console_widget.btnRec.toggle),
        ]
        for seq, cb in bindings:
            sc = QShortcut(QKeySequence(seq), w)
            sc.activated.connect(cb)
            self._shortcuts.append(sc)

        self._dim_key_filter = DimKeyFilter(self.console_widget, w)
        QApplication.instance().installEventFilter(self._dim_key_filter)

    def _open_recordings_folder(self):
        open_folder(RECORDINGS_DIR)

    def _open_qpwgraph(self):
        try:
            subprocess.Popen(["qpwgraph"])
        except FileNotFoundError:
            QMessageBox.information(
                self.app.window,
                "qpwgraph no encontrado",
                "Instala qpwgraph (sudo pacman -S qpwgraph) para abrir la matriz de audio PipeWire/JACK.",
            )

    def _show_shortcuts_help(self):
        QMessageBox.information(
            self.app.window,
            "Atajos de Teclado — Consola DJ",
            "<b>Atajos globales de cabina:</b><br><br>"
            "• <b>Espacio</b>: GO (lanzar siguiente cue en Lista)<br>"
            "• <b>Esc</b>: Fade-Out suave a todos los cues<br>"
            "• <b>Ctrl + Shift + S</b>: STOP ALL (parada inmediata)<br>"
            "• <b>Ctrl + X</b>: Crossfade rápido al cue seleccionado/siguiente<br>"
            "• <b>Tecla 0</b> (o <b>Ctrl + D</b>): Activar / Desactivar DIM (-12 dB para voz)<br>"
            "• <b>Ctrl + Shift + M</b>: Activar / Desactivar MUTE maestro<br>"
            "• <b>Ctrl + R</b>: Iniciar / Detener grabación MP3 (● REC)<br>"
            "• <b>Ctrl + Shift + E</b>: Auto-Master inteligente (EQ 10B + Compresión + Nivel)<br>"
            "• <b>Ctrl + Y</b>: Descargar audio o video HD de YouTube<br>"
            "• <b>Ctrl + Shift + R</b>: Radio Streaming (Icecast / Sintonizador)<br>"
            "• <b>Ctrl + Shift + V</b>: Video y Pantalla de Escenario / Proyector<br>"
            "• <b>Ctrl + Shift + L</b>: Transmisión en Vivo (Cámara / YouTube / Facebook)<br>"
            "• <b>Teclas 1 – 8</b>: Disparar pads del Banco Sampler FX (<b>9</b> = Stop FX)",
        )

    def _toggle_dock(self, visible):
        if self.dock is not None:
            self.dock.setVisible(visible)
            self.Config["show_dock"] = bool(visible)
            self.Config.write()

    def finalize(self):
        if self._dim_key_filter is not None:
            app_inst = QApplication.instance()
            if app_inst is not None:
                app_inst.removeEventFilter(self._dim_key_filter)
        for sc in self._shortcuts:
            sc.setEnabled(False)
        if self.console_widget is not None:
            self.console_widget.shutdown()
        if self.dock is not None:
            self.app.window.removeDockWidget(self.dock)
            self.dock.deleteLater()
        if self.menu is not None:
            self.app.window.menuTools.removeAction(self.menu.menuAction())
        super().finalize()
