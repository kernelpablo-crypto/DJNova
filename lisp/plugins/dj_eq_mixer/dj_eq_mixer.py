# -*- coding: utf-8 -*-
import logging

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QAction,
    QDial,
    QDockWidget,
    QFrame,
    QGridLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState
from lisp.cues.media_cue import MediaCue

logger = logging.getLogger(__name__)


class EqBandWidget(QFrame):
    """Single rotary DJ EQ band (Dial + dB label + KILL button)."""

    def __init__(self, title, accent_color, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #0D1119; border: 1px solid #263247; border-radius: 6px; }"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 3, 5, 3)
        lay.setSpacing(2)

        lbl = QLabel(title, self)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"border: none; color: {accent_color}; font-weight: bold; font-size: 9pt; letter-spacing: 1px;"
        )
        lay.addWidget(lbl)

        self.dial = QDial(self)
        self.dial.setRange(-24, 12)
        self.dial.setValue(0)
        self.dial.setNotchesVisible(True)
        self.dial.setNotchTarget(6.0)
        self.dial.setFixedSize(44, 44)
        self.dial.setToolTip(f"Ganancia {title} (-24 dB a +12 dB). Doble clic en RESET para 0 dB")
        self.dial.valueChanged.connect(self._changed)
        lay.addWidget(self.dial, alignment=Qt.AlignCenter)

        self.valLabel = QLabel("0 dB", self)
        self.valLabel.setAlignment(Qt.AlignCenter)
        self.valLabel.setStyleSheet(
            "border: none; font-family: monospace; font-size: 9pt; color: #DCE6F2; font-weight: bold;"
        )
        lay.addWidget(self.valLabel)

        self.killBtn = QPushButton("KILL", self)
        self.killBtn.setCheckable(True)
        self.killBtn.setFixedHeight(22)
        self.killBtn.setStyleSheet(
            """
            QPushButton {
                background-color: #1A2130; border: 1px solid #3A4B66; border-radius: 3px;
                color: #9AA9C2; font-size: 8pt; font-weight: bold; padding: 1px;
            }
            QPushButton:checked {
                background-color: #D91E36; border: 1px solid #FF6B81; color: #FFFFFF;
            }
            """
        )
        self.killBtn.toggled.connect(self._changed)
        lay.addWidget(self.killBtn)

    def effective_db(self):
        if self.killBtn.isChecked():
            return -24
        return int(self.dial.value())

    def set_state(self, db_val, killed=False):
        self.dial.blockSignals(True)
        self.killBtn.blockSignals(True)
        self.dial.setValue(int(max(-24, min(12, round(db_val)))))
        self.killBtn.setChecked(bool(killed))
        self.dial.blockSignals(False)
        self.killBtn.blockSignals(False)
        self._update_label()

    def _update_label(self):
        if self.killBtn.isChecked():
            self.valLabel.setText("KILL")
            self.valLabel.setStyleSheet(
                "border: none; font-family: monospace; font-size: 9pt; color: #FF4766; font-weight: bold;"
            )
        else:
            v = self.dial.value()
            self.valLabel.setText(f"{v:+d} dB" if v != 0 else "0 dB")
            self.valLabel.setStyleSheet(
                "border: none; font-family: monospace; font-size: 9pt; color: #DCE6F2; font-weight: bold;"
            )

    def _changed(self, *_):
        self._update_label()
        self._on_change()


class DJEqWidget(QFrame):
    """Vertical 3-Band DJ Kill EQ channel strip."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.app = plugin.app
        self.setFixedWidth(122)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 6, 5, 6)
        lay.setSpacing(6)

        self.highBand = EqBandWidget("AGUDOS", "#00D4FF", self.apply_to_running, self)
        self.midBand = EqBandWidget("MEDIOS", "#00E676", self.apply_to_running, self)
        self.lowBand = EqBandWidget("GRAVES", "#FFB300", self.apply_to_running, self)

        lay.addWidget(self.highBand)
        lay.addWidget(self.midBand)
        lay.addWidget(self.lowBand)

        # Quick Presets Grid
        grid = QGridLayout()
        grid.setSpacing(4)
        presets = [
            ("FLAT", (0, 0, 0)),
            ("BASS", (5, -1, 2)),
            ("VOZ", (-4, 4, 2)),
            ("RADIO", (-18, 6, -12)),
        ]
        for idx, (label, vals) in enumerate(presets):
            btn = QPushButton(label, self)
            btn.setFixedHeight(24)
            btn.setStyleSheet("font-size: 8pt; padding: 2px;")
            btn.clicked.connect(lambda _=False, v=vals: self.apply_preset(*v))
            grid.addWidget(btn, idx // 2, idx % 2)
        lay.addLayout(grid)

        lay.addStretch(1)

        # Sync running cues periodically so newly started cues pick up live EQ
        self._base_eq = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.apply_to_running)
        self._timer.start(300)

    def invalidate_base_eq(self, cue_id=None):
        """Called by Auto-Master when a cue's base 10-band EQ curve changes."""
        if cue_id is None:
            self._base_eq.clear()
        else:
            self._base_eq.pop(cue_id, None)

    def apply_preset(self, low, mid, high):
        self.lowBand.set_state(low, False)
        self.midBand.set_state(mid, False)
        self.highBand.set_state(high, False)
        self.apply_to_running()

    def get_state(self):
        return {
            "low": self.lowBand.dial.value(),
            "mid": self.midBand.dial.value(),
            "high": self.highBand.dial.value(),
            "kill_low": self.lowBand.killBtn.isChecked(),
            "kill_mid": self.midBand.killBtn.isChecked(),
            "kill_high": self.highBand.killBtn.isChecked(),
        }

    def set_state(self, low=None, mid=None, high=None, kill_low=None, kill_mid=None, kill_high=None):
        if low is not None or kill_low is not None:
            self.lowBand.set_state(
                low if low is not None else self.lowBand.dial.value(),
                kill_low if kill_low is not None else self.lowBand.killBtn.isChecked(),
            )
        if mid is not None or kill_mid is not None:
            self.midBand.set_state(
                mid if mid is not None else self.midBand.dial.value(),
                kill_mid if kill_mid is not None else self.midBand.killBtn.isChecked(),
            )
        if high is not None or kill_high is not None:
            self.highBand.set_state(
                high if high is not None else self.highBand.dial.value(),
                kill_high if kill_high is not None else self.highBand.killBtn.isChecked(),
            )
        self.apply_to_running()

    def apply_to_running(self):
        if self.app.session is None:
            return
        layout = self.app.layout
        if layout is None:
            return
        kill_low = self.lowBand.killBtn.isChecked()
        kill_mid = self.midBand.killBtn.isChecked()
        kill_high = self.highBand.killBtn.isChecked()
        low = self.lowBand.dial.value()
        mid = self.midBand.dial.value()
        high = self.highBand.dial.value()

        is_neutral = (
            not kill_low and not kill_mid and not kill_high
            and low == 0 and mid == 0 and high == 0
        )

        try:
            for cue in layout.cues(MediaCue):
                eq = cue.media.element("Equalizer10")
                if eq is None:
                    continue

                if is_neutral:
                    # Restore cue's original/Auto-Master EQ once if we had altered it, otherwise never touch it!
                    if cue.id in self._base_eq:
                        base = self._base_eq.pop(cue.id)
                        for i in range(10):
                            setattr(eq, f"band{i}", int(base[i]))
                    continue

                if not (cue.state & CueState.Running):
                    continue

                # Snapshot cue's base 10-band EQ before applying live DJ 3-band offsets
                if cue.id not in self._base_eq:
                    self._base_eq[cue.id] = [int(round(getattr(eq, f"band{i}", 0))) for i in range(10)]

                base = self._base_eq[cue.id]
                for i in (0, 1, 2):
                    val = -24 if kill_low else max(-24, min(12, base[i] + low))
                    if getattr(eq, f"band{i}") != val:
                        setattr(eq, f"band{i}", int(val))
                for i in (3, 4, 5, 6):
                    val = -24 if kill_mid else max(-24, min(12, base[i] + mid))
                    if getattr(eq, f"band{i}") != val:
                        setattr(eq, f"band{i}", int(val))
                for i in (7, 8, 9):
                    val = -24 if kill_high else max(-24, min(12, base[i] + high))
                    if getattr(eq, f"band{i}") != val:
                        setattr(eq, f"band{i}", int(val))
        except Exception:
            pass

    def shutdown(self):
        self._timer.stop()


class DjEqMixer(Plugin):
    """Ecualizador DJ de 3 Bandas (LOW/MID/HIGH + KILL) en vivo."""

    Name = "Ecualizador DJ 3 Bandas"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Añade un rack vertical de ecualización DJ en vivo (Graves, Medios, Agudos "
        "con botones KILL instantáneos y presets rápidos) sobre las pistas en reproducción."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        window = self.app.window

        self.dock = QDockWidget("EQ DJ 3B", window)
        self.dock.setObjectName("DJEqMixerDock")
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        self.eq_widget = DJEqWidget(self, self.dock)
        self.dock.setWidget(self.eq_widget)
        window.addDockWidget(Qt.LeftDockWidgetArea, self.dock)

        if not self.Config.get("show_dock", True):
            self.dock.hide()

        self.action = QAction("Mostrar Ecualizador DJ 3 Bandas", window.menuTools)
        self.action.setCheckable(True)
        self.action.setChecked(self.dock.isVisible())
        self.action.toggled.connect(self._toggle_dock)
        window.menuTools.addAction(self.action)

    def _toggle_dock(self, visible):
        self.dock.setVisible(visible)
        self.Config["show_dock"] = bool(visible)
        self.Config.write()

    def finalize(self):
        if self.eq_widget is not None:
            self.eq_widget.shutdown()
        if self.dock is not None:
            self.app.window.removeDockWidget(self.dock)
            self.dock.deleteLater()
        if self.action is not None:
            self.app.window.menuTools.removeAction(self.action)
        super().finalize()
