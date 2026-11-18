# -*- coding: utf-8 -*-
import logging

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QTextEdit,
    QVBoxLayout,
)

from lisp import plugins
from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState
from lisp.cues.media_cue import MediaCue

from .analyzer import BAND_LABELS, analyze_audio_file, uri_to_local_path

logger = logging.getLogger(__name__)


def ensure_master_elements(cue):
    """Ensure Equalizer10 and AudioDynamic exist in cue pipeline (returns True if complete)."""
    if not isinstance(cue, MediaCue):
        return False
    pipe = list(cue.media.pipe)
    needs_eq = "Equalizer10" not in pipe
    needs_dyn = "AudioDynamic" not in pipe
    if not needs_eq and not needs_dyn:
        return True

    if cue.state & CueState.Running:
        return False

    insert_pos = len(pipe) - 1
    if "DbMeter" in pipe:
        insert_pos = pipe.index("DbMeter")

    if needs_eq:
        pipe.insert(insert_pos, "Equalizer10")
        insert_pos += 1
    if needs_dyn:
        pipe.insert(insert_pos, "AudioDynamic")

    cue.media.pipe = tuple(pipe)
    return True


def apply_master_result(cue, res):
    """Apply analyzed EQ, Compression, and Volume leveling directly to a MediaCue."""
    pipeline_ready = ensure_master_elements(cue)

    # Clear live DJ EQ cached base for this cue so it won't revert our new EQ
    try:
        eqp = plugins.get_plugin("DjEqMixer")
        if eqp and eqp.eq_widget and hasattr(eqp.eq_widget, "invalidate_base_eq"):
            eqp.eq_widget.invalidate_base_eq(cue.id)
    except Exception:
        pass

    eq = cue.media.element("Equalizer10")
    if eq is not None:
        for i, val in enumerate(res["eq_bands"]):
            setattr(eq, f"band{i}", int(round(val)))

    dyn = cue.media.element("AudioDynamic")
    if dyn is not None:
        c = res["compressor"]
        dyn.mode = c["mode"]
        dyn.characteristics = c["characteristics"]
        dyn.threshold = float(c["threshold"])
        dyn.ratio = float(c["ratio"])

    vol = cue.media.element("Volume")
    if vol is not None:
        vol.volume = float(res["volume"])
        if cue.state & CueState.Running:
            vol.live_volume = float(res["volume"])

    # If the cue was running and lacked AudioDynamic, auto-attach it once the cue stops
    if not pipeline_ready:
        def _deferred_apply(*_):
            try:
                cue.stopped.disconnect(_deferred_apply)
                cue.end.disconnect(_deferred_apply)
            except Exception:
                pass
            apply_master_result(cue, res)

        try:
            cue.stopped.connect(_deferred_apply)
            cue.end.connect(_deferred_apply)
        except Exception:
            pass
    return pipeline_ready


def reset_cue_master(cue):
    """Reset EQ to flat 0 dB, disable compression (ratio=1), and restore volume to 1.0."""
    if not isinstance(cue, MediaCue):
        return
    eq = cue.media.element("Equalizer10")
    if eq is not None:
        for i in range(10):
            setattr(eq, f"band{i}", 0)
    dyn = cue.media.element("AudioDynamic")
    if dyn is not None:
        dyn.ratio = 1.0
        dyn.threshold = 0.0
    vol = cue.media.element("Volume")
    if vol is not None:
        vol.volume = 1.0
        if cue.state & CueState.Running:
            vol.live_volume = 1.0


class MasterWorker(QThread):
    progress = pyqtSignal(int, int, str)
    cue_done = pyqtSignal(object, dict)
    cue_error = pyqtSignal(object, str)
    all_finished = pyqtSignal(list)

    def __init__(self, cues, target_rms_db=-14.0):
        super().__init__()
        self.cues = list(cues)
        self.target_rms_db = target_rms_db

    def run(self):
        summary = []
        total = len(self.cues)
        for idx, cue in enumerate(self.cues, 1):
            name = cue.name or f"Cue {idx}"
            self.progress.emit(idx, total, f"Analizando: {name}...")
            try:
                uri = cue.media.input_uri()
                path = uri_to_local_path(uri)
                res = analyze_audio_file(path, target_rms_db=self.target_rms_db)
                self.cue_done.emit(cue, res)
                summary.append((name, res, None))
            except Exception as exc:
                logger.exception(f"Auto-Master falló en '{name}'")
                self.cue_error.emit(cue, str(exc))
                summary.append((name, None, str(exc)))
        self.all_finished.emit(summary)


class MasterProgressDialog(QDialog):
    def __init__(self, total, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto-Master Inteligente (EQ + Compresión)")
        self.setMinimumWidth(480)
        self.setMinimumHeight(260)

        lay = QVBoxLayout(self)
        self.statusLabel = QLabel("Iniciando análisis espectral FFT...", self)
        self.statusLabel.setStyleSheet("font-weight: bold; color: #00D4FF;")
        lay.addWidget(self.statusLabel)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        self.logBox = QTextEdit(self)
        self.logBox.setReadOnly(True)
        self.logBox.setStyleSheet("font-family: monospace; font-size: 9pt;")
        lay.addWidget(self.logBox)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btnClose = QPushButton("Cerrar", self)
        self.btnClose.setEnabled(False)
        self.btnClose.clicked.connect(self.accept)
        btns.addWidget(self.btnClose)
        lay.addLayout(btns)

    def on_progress(self, idx, total, text):
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(idx - 1)
        self.statusLabel.setText(f"[{idx}/{total}] {text}")

    def append_result(self, name, res, deferred_comp=False):
        eq_str = " ".join(f"{lbl}:{v:+.0f}" for lbl, v in zip(BAND_LABELS, res["eq_bands"]))
        c = res["compressor"]
        note = (
            '<br>&nbsp;&nbsp;<span style="color:#FFB300;">⏳ Pista en reproducción: el bloque Compresor/Expansor '
            'se añadirá automáticamente al detenerla (EQ y Volumen ya aplicados).</span>'
            if deferred_comp
            else ""
        )
        self.logBox.append(
            f'<b style="color:#00E676;">✔ {name}</b><br>'
            f'&nbsp;&nbsp;Perfil: <b style="color:#FFB300;">{res["profile"]}</b> | '
            f'RMS: {res["rms_db"]} dB | Pico: {res["peak_db"]} dB | Dinámica: {res["crest_db"]} dB<br>'
            f'&nbsp;&nbsp;Volumen: {res["gain_db"]:+.1f} dB | Compresor: Soft-Knee (Thr {c["threshold"]}, Ratio {c["ratio"]})<br>'
            f'&nbsp;&nbsp;EQ 10B: <span style="color:#80F2FF;">{eq_str}</span>{note}<br>'
        )

    def append_error(self, name, err):
        self.logBox.append(f'<b style="color:#FF4766;">✖ {name}:</b> {err}<br>')

    def on_finished(self, summary):
        self.bar.setValue(self.bar.maximum())
        ok_cnt = sum(1 for _, r, _ in summary if r is not None)
        self.statusLabel.setText(f"¡Listo! {ok_cnt}/{len(summary)} pista(s) masterizadas automáticamente.")
        self.btnClose.setEnabled(True)


class DjAutoMaster(Plugin):
    """Auto-Master inteligente: detecta el perfil de cada pista y aplica EQ 10B + Compresión + Nivel."""

    Name = "Auto-Master Inteligente (EQ + Compresión)"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Analiza el espectro FFT y la dinámica de cada pista para detectar su perfil "
        "y configurar automáticamente Equalizer10, Compresor AudioDynamic y nivelación a -14 dB."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        self._workers = []
        self._eq_button = None
        self._shortcut = None

        window = self.app.window
        self.menu = QMenu("Auto-Master Inteligente (EQ + Compresión)", window.menuTools)

        act_sel = QAction("Analizar y Ecualizar Cue(s) Seleccionados (Ctrl+Shift+E)", self.menu)
        act_sel.triggered.connect(self.master_selected_or_active)
        self.menu.addAction(act_sel)

        act_all = QAction("Auto-Master a TODA la sesión...", self.menu)
        act_all.triggered.connect(self.master_all_session)
        self.menu.addAction(act_all)

        self.menu.addSeparator()

        act_reset = QAction("Restaurar EQ y Compresión a plano (Cue seleccionado)", self.menu)
        act_reset.triggered.connect(self.reset_selected)
        self.menu.addAction(act_reset)

        window.menuTools.addMenu(self.menu)

        self._shortcut = QShortcut(QKeySequence("Ctrl+Shift+E"), window)
        self._shortcut.activated.connect(self.master_selected_or_active)

        # Inject a sleek AUTO EQ button into the left EQ DJ 3B dock once all plugins finish loading
        QTimer.singleShot(400, self._attach_eq_dock_button)
        # Sanitize any legacy float EQ band values on session load to prevent Qt5 QSlider TypeError
        self.app.session_loaded.connect(self._sanitize_session_eq)

    def _sanitize_session_eq(self, *_):
        if self.app.session is None or self.app.layout is None:
            return
        for cue in self.app.layout.cues(MediaCue):
            eq = cue.media.element("Equalizer10")
            if eq is not None:
                for i in range(10):
                    v = getattr(eq, f"band{i}", 0)
                    if isinstance(v, float):
                        setattr(eq, f"band{i}", int(round(v)))

    def _attach_eq_dock_button(self):
        try:
            eq_plugin = plugins.get_plugin("DjEqMixer")
            if eq_plugin and eq_plugin.eq_widget:
                btn = QPushButton("⚡ AUTO EQ", eq_plugin.eq_widget)
                btn.setFixedHeight(28)
                btn.setToolTip(
                    "Analiza el tema seleccionado (o sonando) y ajusta automáticamente "
                    "EQ de 10 bandas, Compresión y Volumen (Ctrl+Shift+E)"
                )
                btn.setStyleSheet(
                    """
                    QPushButton {
                        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00C864, stop:1 #007A3D);
                        border: 1px solid #00FF9D; border-radius: 5px;
                        color: #FFFFFF; font-weight: bold; font-size: 8.5pt; padding: 3px;
                    }
                    QPushButton:hover { background-color: #00E676; color: #050A10; }
                    """
                )
                btn.clicked.connect(self.master_selected_or_active)
                eq_plugin.eq_widget.layout().insertWidget(0, btn)
                self._eq_button = btn
        except Exception:
            pass

    def _target_cues(self):
        if self.app.session is None or self.app.layout is None:
            return []
        layout = self.app.layout
        selected = [c for c in layout.selected_cues() if isinstance(c, MediaCue)]
        if selected:
            return selected
        running = [c for c in layout.cues(MediaCue) if c.state & CueState.Running]
        if running:
            return running
        try:
            sb = layout.standby_cue()
            if isinstance(sb, MediaCue):
                return [sb]
        except Exception:
            pass
        return []

    def master_selected_or_active(self):
        cues = self._target_cues()
        if not cues:
            QMessageBox.information(
                self.app.window,
                "Auto-Master Inteligente",
                "Selecciona primero una o más pistas multimedia (o ten una sonando) para analizarla.",
            )
            return
        self._run_batch(cues)

    def master_all_session(self):
        if self.app.session is None or self.app.layout is None:
            return
        cues = list(self.app.layout.cues(MediaCue))
        if not cues:
            QMessageBox.information(
                self.app.window,
                "Auto-Master Inteligente",
                "No hay pistas multimedia en la sesión actual.",
            )
            return
        self._run_batch(cues)

    def auto_master_single_silent(self, cue, on_done=None):
        """Background single-cue auto-master (used by YouTube downloader)."""
        target = float(self.Config.get("target_rms_db", -14.0))
        worker = MasterWorker([cue], target_rms_db=target)

        def _apply(c, res):
            apply_master_result(c, res)
            if on_done:
                on_done(res, None)

        def _err(c, err):
            if on_done:
                on_done(None, err)

        worker.cue_done.connect(_apply)
        worker.cue_error.connect(_err)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._workers.append(worker)
        worker.start()

    def _run_batch(self, cues):
        target = float(self.Config.get("target_rms_db", -14.0))
        dlg = MasterProgressDialog(len(cues), self.app.window)
        worker = MasterWorker(cues, target_rms_db=target)

        worker.progress.connect(dlg.on_progress)

        def _on_cue_done(cue, res):
            ready = apply_master_result(cue, res)
            dlg.append_result(cue.name or "Cue", res, deferred_comp=not ready)

        worker.cue_done.connect(_on_cue_done)
        worker.cue_error.connect(lambda c, e: dlg.append_error(c.name or "Cue", e))
        worker.all_finished.connect(dlg.on_finished)
        worker.finished.connect(lambda: self._cleanup_worker(worker))

        self._workers.append(worker)
        worker.start()
        dlg.exec_()

    def reset_selected(self):
        cues = self._target_cues()
        for cue in cues:
            reset_cue_master(cue)
        if cues:
            QMessageBox.information(
                self.app.window,
                "Auto-Master Inteligente",
                f"Se restauró el EQ y la compresión a plano en {len(cues)} pista(s).",
            )

    def _cleanup_worker(self, w):
        if w in self._workers:
            self._workers.remove(w)

    def finalize(self):
        if self._shortcut is not None:
            self._shortcut.setEnabled(False)
        if self._eq_button is not None:
            self._eq_button.deleteLater()
        if self.menu is not None:
            self.app.window.menuTools.removeAction(self.menu.menuAction())
        super().finalize()
