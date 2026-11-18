# -*- coding: utf-8 -*-
import logging
from functools import partial

from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import QAction, QMenu

from lisp.core.plugin import Plugin

logger = logging.getLogger(__name__)

DJ_PALETTE = [
    ("Cian Neón (Intro / Jingle)", "#0E4F6B", "#00D4FF", "#FFFFFF"),
    ("Verde Club (Música / Base)", "#0E5734", "#00E676", "#FFFFFF"),
    ("Ámbar Cabina (Efecto / SFX)", "#6E4306", "#FFB300", "#FFFFFF"),
    ("Magenta DJ (Voces / Drop)", "#5E1159", "#FF3EDB", "#FFFFFF"),
    ("Rojo Alerta (Urgente / Cortina)", "#6E111E", "#FF2A4B", "#FFFFFF"),
    ("Azul Profundo (Ambiente)", "#122E6E", "#4D88FF", "#FFFFFF"),
]


def _color_icon(hex_color):
    pm = QPixmap(14, 14)
    pm.fill(QColor(hex_color))
    return QIcon(pm)


class DjPadColors(Plugin):
    """Paleta rápida de colores estilo pads DJ para cues seleccionados."""

    Name = "Colores de Pads DJ"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Permite colorear instantáneamente los pads/cues seleccionados con una "
        "paleta neón estilo controlador DJ (Intro, Música, SFX, Voces, Alerta)."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        self.menu = QMenu("Colorear Pads DJ", self.app.window.menuTools)

        for label, bg, border, fg in DJ_PALETTE:
            action = QAction(_color_icon(border), label, self.menu)
            action.triggered.connect(partial(self._apply_color, bg, border, fg))
            self.menu.addAction(action)

        self.menu.addSeparator()
        reset_action = QAction("Restaurar estilo por defecto", self.menu)
        reset_action.triggered.connect(self._reset_color)
        self.menu.addAction(reset_action)

        self.app.window.menuTools.addMenu(self.menu)

    def _apply_color(self, bg, border, fg):
        layout = self.app.layout if self.app.session is not None else None
        if layout is None:
            return
        css = f"background-color: {bg}; border: 2px solid {border}; color: {fg};"
        for cue in layout.selected_cues():
            cue.stylesheet = css

    def _reset_color(self):
        layout = self.app.layout if self.app.session is not None else None
        if layout is None:
            return
        for cue in layout.selected_cues():
            cue.stylesheet = ""

    def finalize(self):
        if self.menu is not None:
            self.app.window.menuTools.removeAction(self.menu.menuAction())
        super().finalize()
