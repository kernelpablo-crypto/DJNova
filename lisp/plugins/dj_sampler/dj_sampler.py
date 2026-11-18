# -*- coding: utf-8 -*-
import json
import logging
import os

from PyQt5.QtCore import QEvent, QObject, Qt, QUrl
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAction,
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTextEdit,
)

from lisp.core.djnova_platform import get_bundled_sfx_dir
from lisp.core.plugin import Plugin
from lisp.plugins.gst_backend.gi_repository import Gst
from lisp.ui.icons import IconTheme

logger = logging.getLogger(__name__)


def resolve_pad_path(raw_path):
    """Resuelve rutas absolutas o nombres de archivo dentro del pack SFX incluido."""
    if raw_path and os.path.exists(raw_path):
        return raw_path
    if raw_path:
        cand = os.path.join(get_bundled_sfx_dir(), os.path.basename(raw_path))
        if os.path.exists(cand):
            return cand
    return ""

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac", ".webm")
PAD_COLORS = [
    ("Cian Neón", "#00D4FF"),
    ("Verde Club", "#00E676"),
    ("Ámbar Cabina", "#FFB300"),
    ("Magenta DJ", "#FF3EDB"),
    ("Rojo Alerta", "#FF2A4B"),
    ("Azul Eléctrico", "#4D88FF"),
]

NUM_KEY_MAP = {
    Qt.Key_1: 0,
    Qt.Key_2: 1,
    Qt.Key_3: 2,
    Qt.Key_4: 3,
    Qt.Key_5: 4,
    Qt.Key_6: 5,
    Qt.Key_7: 6,
    Qt.Key_8: 7,
}


class SamplerKeyFilter(QObject):
    """Smart event filter: triggers Sampler FX on keys 1-8 and 0 (STOP) ONLY when not typing."""

    def __init__(self, sampler_widget, main_window):
        super().__init__(main_window)
        self.sampler = sampler_widget
        self.main_window = main_window

    def _is_typing_context(self):
        # Never fire if a modal dialog or another top-level dialog is active
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
            if not self.sampler.keys_enabled:
                return False

            # Ignore if Ctrl, Alt, or Meta modifiers are pressed (allow KeypadModifier)
            mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
            if mods:
                return False

            key = event.key()
            if key in NUM_KEY_MAP or key == Qt.Key_9:
                if self._is_typing_context():
                    return False
                if key == Qt.Key_9:
                    self.sampler.stop_all()
                else:
                    idx = NUM_KEY_MAP[key]
                    if 0 <= idx < len(self.sampler.pads):
                        self.sampler.pads[idx].trigger_pad()
                return True
        return False


class FxPadButton(QPushButton):
    def __init__(self, idx, pad_data, sampler_widget, parent=None):
        super().__init__(parent)
        self.idx = idx
        self.sampler = sampler_widget
        self.pad_data = dict(pad_data)
        self._player = None

        self.setFixedHeight(38)
        self.setMinimumWidth(90)
        self.setFocusPolicy(Qt.NoFocus)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_menu)
        self.clicked.connect(self.trigger_pad)
        self._refresh_ui()

    def _refresh_ui(self):
        name = self.pad_data.get("name") or f"FX {self.idx + 1}"
        path = resolve_pad_path(self.pad_data.get("path") or "")
        color = self.pad_data.get("color") or "#00D4FF"
        key_hint = f"{self.idx + 1}"

        if path and os.path.exists(path):
            self.setText(f"[{key_hint}] {name}")
            self.setToolTip(
                f"{name}\nTecla: {key_hint} | Archivo: {path}\nClic: disparar | Clic derecho: editar"
            )
            self.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1D2738, stop:1 #0F1520);
                    border: 2px solid {color};
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-weight: bold;
                    font-size: 8.5pt;
                    padding: 3px 6px;
                }}
                QPushButton:hover {{
                    background-color: #25344D;
                }}
                QPushButton:pressed {{
                    background-color: {color};
                    color: #05080D;
                }}
                """
            )
        else:
            self.setText(f"[{key_hint}] + Vacío")
            self.setToolTip(f"Tecla {key_hint} — Clic o clic derecho para asignar un efecto de sonido")
            self.setStyleSheet(
                """
                QPushButton {
                    background-color: #10141D;
                    border: 1px dashed #36455E;
                    border-radius: 6px;
                    color: #6B7C96;
                    font-size: 8pt;
                    padding: 3px 6px;
                }
                QPushButton:hover {
                    border: 1px dashed #00D4FF;
                    color: #DCE6F2;
                }
                """
            )

    def trigger_pad(self):
        path = resolve_pad_path(self.pad_data.get("path") or "")
        if not path:
            self._assign_file()
            return

        self.stop_pad()
        try:
            player = Gst.ElementFactory.make("playbin", None)
            if player is None:
                return
            uri = QUrl.fromLocalFile(os.path.abspath(path)).toString()
            player.set_property("uri", uri)
            player.set_property("volume", self.sampler.volume_gain())
            player.set_state(Gst.State.PLAYING)
            self._player = player
        except Exception:
            logger.exception(f"Error reproduciendo FX pad {self.idx + 1}")

    def stop_pad(self):
        if self._player is not None:
            try:
                self._player.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._player = None

    def set_volume(self, gain):
        if self._player is not None:
            try:
                self._player.set_property("volume", gain)
            except Exception:
                pass

    def _assign_file(self):
        resolved = resolve_pad_path(self.pad_data.get("path") or "")
        start_dir = os.path.dirname(resolved) if resolved else get_bundled_sfx_dir()
        fname, _ = QFileDialog.getOpenFileName(
            self,
            f"Asignar efecto de sonido al Pad [{self.idx + 1}]",
            start_dir,
            "Audio (*.mp3 *.wav *.ogg *.flac *.m4a *.opus *.aac);;Todos (*)",
        )
        if fname:
            self.pad_data["path"] = fname
            base = os.path.splitext(os.path.basename(fname))[0]
            self.pad_data["name"] = base[:14]
            self._refresh_ui()
            self.sampler.save_pads()

    def _rename_pad(self):
        text, ok = QInputDialog.getText(
            self,
            f"Renombrar Pad [{self.idx + 1}]",
            "Nombre del pad:",
            text=self.pad_data.get("name", ""),
        )
        if ok and text.strip():
            self.pad_data["name"] = text.strip()[:18]
            self._refresh_ui()
            self.sampler.save_pads()

    def _set_color(self, hex_color):
        self.pad_data["color"] = hex_color
        self._refresh_ui()
        self.sampler.save_pads()

    def _clear_pad(self):
        self.stop_pad()
        self.pad_data["path"] = ""
        self.pad_data["name"] = f"FX {self.idx + 1}"
        self._refresh_ui()
        self.sampler.save_pads()

    def _open_menu(self, pos):
        menu = QMenu(self)
        act_assign = menu.addAction("Asignar archivo de audio...")
        act_assign.triggered.connect(self._assign_file)

        act_rename = menu.addAction("Renombrar pad...")
        act_rename.triggered.connect(self._rename_pad)

        color_menu = menu.addMenu("Color del pad")
        for cname, chex in PAD_COLORS:
            a = color_menu.addAction(cname)
            a.triggered.connect(lambda _=False, c=chex: self._set_color(c))

        menu.addSeparator()
        act_clear = menu.addAction("Limpiar pad")
        act_clear.triggered.connect(self._clear_pad)

        act_defaults = menu.addAction("Restaurar los 8 FX DJ por defecto")
        act_defaults.triggered.connect(self.sampler.restore_default_pack)

        menu.exec_(self.mapToGlobal(pos))


class DJSamplerWidget(QFrame):
    """8-Pad instant FX Soundboard rack triggered by number keys 1-8 (and 0 for STOP)."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setObjectName("DJConsolePanel")
        self.keys_enabled = bool(self.plugin.Config.get("keys_enabled", True))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(5)

        title = QLabel("SAMPLER", self)
        title.setStyleSheet("color: #00D4FF; font-weight: bold; font-size: 8.5pt; letter-spacing: 1px;")
        lay.addWidget(title)

        pads_cfg = self.plugin.Config.get("pads", [])
        if len(pads_cfg) < 8:
            default_colors = ["#00D4FF", "#00E676", "#FFB300", "#FF3EDB", "#FF2A4B", "#4D88FF", "#00E5A0", "#FF7043"]
            for i in range(len(pads_cfg), 8):
                pads_cfg.append({"name": f"FX {i + 1}", "path": "", "color": default_colors[i % 8]})

        self.pads = []
        for idx in range(8):
            btn = FxPadButton(idx, pads_cfg[idx], self, self)
            self.pads.append(btn)
            lay.addWidget(btn, 1)

        # Quick 1-8 keyboard lock/unlock toggle
        self.btnKeysLock = QPushButton("1-8", self)
        self.btnKeysLock.setCheckable(True)
        self.btnKeysLock.setChecked(self.keys_enabled)
        self.btnKeysLock.setFixedWidth(38)
        self.btnKeysLock.setFocusPolicy(Qt.NoFocus)
        self.btnKeysLock.setToolTip(
            "Activar / Desactivar disparo rápido con teclas numéricas 1–8 (9 = Stop FX)"
        )
        self.btnKeysLock.setStyleSheet(
            """
            QPushButton {
                background-color: #161B26; border: 1px solid #3A4B66; border-radius: 4px;
                color: #7A8CA8; font-weight: bold; font-size: 8pt; padding: 3px;
            }
            QPushButton:checked {
                background-color: #0C3B4E; border: 1px solid #00D4FF; color: #00E5FF;
            }
            """
        )
        self.btnKeysLock.toggled.connect(self._on_keys_toggled)
        lay.addWidget(self.btnKeysLock)

        # Folder loader
        btnFolder = QPushButton(IconTheme.get("folder-open"), "", self)
        btnFolder.setFixedWidth(30)
        btnFolder.setFocusPolicy(Qt.NoFocus)
        btnFolder.setToolTip("Cargar hasta 8 efectos de audio desde una carpeta")
        btnFolder.clicked.connect(self._load_folder)
        lay.addWidget(btnFolder)

        self.volSlider = QSlider(Qt.Horizontal, self)
        self.volSlider.setRange(0, 100)
        self.volSlider.setValue(int(self.plugin.Config.get("volume", 85)))
        self.volSlider.setFixedWidth(66)
        self.volSlider.setFocusPolicy(Qt.NoFocus)
        self.volSlider.setToolTip("Volumen del Banco Sampler FX")
        self.volSlider.valueChanged.connect(self._on_vol_changed)
        lay.addWidget(self.volSlider)

        btnStop = QPushButton("STOP [9]", self)
        btnStop.setObjectName("DJBtnPanic")
        btnStop.setFocusPolicy(Qt.NoFocus)
        btnStop.setToolTip("Detener todos los efectos del Sampler (Tecla 9)")
        btnStop.clicked.connect(self.stop_all)
        lay.addWidget(btnStop)

        # Install smart application-wide key filter (safe when typing in any text/number field!)
        self._key_filter = SamplerKeyFilter(self, self.plugin.app.window)
        QApplication.instance().installEventFilter(self._key_filter)

    def _on_keys_toggled(self, checked):
        self.keys_enabled = bool(checked)
        self.plugin.Config["keys_enabled"] = self.keys_enabled
        self.plugin.Config.write()

    def volume_gain(self):
        return self.volSlider.value() / 100.0

    def _on_vol_changed(self, val):
        gain = val / 100.0
        for p in self.pads:
            p.set_volume(gain)
        self.plugin.Config["volume"] = int(val)
        self.plugin.Config.write()

    def _load_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de efectos de sonido", get_bundled_sfx_dir()
        )
        if not folder:
            return
        files = sorted(
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(AUDIO_EXTS) and os.path.isfile(os.path.join(folder, f))
        )[:8]
        for idx, fpath in enumerate(files):
            self.pads[idx].pad_data["path"] = fpath
            self.pads[idx].pad_data["name"] = os.path.splitext(os.path.basename(fpath))[0][:14]
            self.pads[idx]._refresh_ui()
        self.save_pads()

    def stop_all(self):
        for p in self.pads:
            p.stop_pad()

    def save_pads(self):
        self.plugin.Config["pads"] = [dict(p.pad_data) for p in self.pads]
        self.plugin.Config.write()

    def restore_default_pack(self):
        default_file = os.path.join(os.path.dirname(__file__), "default.json")
        if not os.path.exists(default_file):
            return
        try:
            with open(default_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            defaults = data.get("pads", [])
            for idx, pad in enumerate(self.pads):
                if idx < len(defaults):
                    pad.stop_pad()
                    pad.pad_data = dict(defaults[idx])
                    pad._refresh_ui()
            self.save_pads()
        except Exception:
            logger.exception("Error restaurando los 8 FX DJ por defecto")

    def shutdown(self):
        self.stop_all()
        if self._key_filter is not None:
            app_inst = QApplication.instance()
            if app_inst is not None:
                app_inst.removeEventFilter(self._key_filter)


class DjSampler(Plugin):
    """Banco Sampler de 8 Pads FX instantáneos (Teclas 1-8, 0 = Stop) independiente."""

    Name = "Banco Sampler FX (8 Pads)"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Añade un rack inferior de 8 botones FX instantáneos (teclas numéricas 1–8 y 0 para Stop) "
        "para disparar efectos, aplausos, bocinas o cortinas encima de cualquier pista."
    )
    Version = "1.1.0"

    def __init__(self, app):
        super().__init__(app)
        window = self.app.window

        self.dock = QDockWidget("SAMPLER FX (Teclas 1–8 | 9: Stop)", window)
        self.dock.setObjectName("DJSamplerDock")
        self.dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        self.sampler_widget = DJSamplerWidget(self, self.dock)
        self.dock.setWidget(self.sampler_widget)
        window.addDockWidget(Qt.BottomDockWidgetArea, self.dock)

        if not self.Config.get("show_dock", True):
            self.dock.hide()

        self.action = QAction("Mostrar Banco Sampler FX (Teclas 1–8)", window.menuTools)
        self.action.setCheckable(True)
        self.action.setChecked(self.dock.isVisible())
        self.action.toggled.connect(self._toggle_dock)
        window.menuTools.addAction(self.action)

    def _toggle_dock(self, visible):
        self.dock.setVisible(visible)
        self.Config["show_dock"] = bool(visible)
        self.Config.write()

    def finalize(self):
        if self.sampler_widget is not None:
            self.sampler_widget.shutdown()
        if self.dock is not None:
            self.app.window.removeDockWidget(self.dock)
            self.dock.deleteLater()
        if self.action is not None:
            self.app.window.menuTools.removeAction(self.action)
        super().finalize()
