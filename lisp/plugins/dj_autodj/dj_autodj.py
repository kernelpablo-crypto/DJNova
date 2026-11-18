# -*- coding: utf-8 -*-
"""Plugin de Playlist Continua / AutoDJ ("Non-Stop Deck") para Linux Show Player.

Transforma el recuadro negro en desuso de InfoPanel (al lado derecho de GO)
en un reproductor continuo automático sin silencios (Zero-Gap / Crossfade),
con soporte para arrastrar temas desde la lista manual, soltar archivos de audio
locales y avanzar automáticamente al siguiente tema al finalizar.
"""
import logging
import os

from PyQt5.QtCore import QEvent, QPoint, QSize, Qt, QTimer, QUrl
from PyQt5.QtGui import QColor, QFont, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from lisp.core.fade_functions import FadeInType, FadeOutType
from lisp.core.plugin import Plugin
from lisp.cues.cue import Cue, CueAction, CueState
from lisp.cues.media_cue import MediaCue
from lisp.layout.cue_layout import CueLayout
from lisp.layout.cue_menu import MENU_PRIORITY_CUE, SimpleMenuAction

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".m4a",
    ".aac",
    ".opus",
    ".wma",
    ".aiff",
}


def format_time_ms(ms):
    """Convierte milisegundos a formato MM:SS o HH:MM:SS."""
    if callable(ms):
        try:
            ms = ms()
        except Exception:
            ms = 0
    if ms is None:
        return "00:00"
    try:
        total_sec = max(0, int(float(ms) // 1000))
    except (ValueError, TypeError):
        return "00:00"
    mins = total_sec // 60
    secs = total_sec % 60
    if mins >= 60:
        hours = mins // 60
        mins = mins % 60
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class AutoDjListWidget(QListWidget):
    """Lista de reproducción que acepta Drag & Drop desde LiSP y archivos externos."""

    def __init__(self, autodj_widget, parent=None):
        super().__init__(parent)
        self.autodj = autodj_widget
        self.setObjectName("AutoDjQueueList")

        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setFocusPolicy(Qt.ClickFocus)

        self.customContextMenuRequested.connect(self._open_context_menu)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.setStyleSheet(
            """
            QListWidget#AutoDjQueueList {
                background-color: #070B12;
                border: 1px solid #182335;
                border-radius: 4px;
                color: #D6E2F0;
                font-size: 8.5pt;
                padding: 1px;
            }
            QListWidget#AutoDjQueueList::item {
                padding: 3px 6px;
                border-bottom: 1px solid #0F1622;
                border-radius: 3px;
                margin-bottom: 1px;
            }
            QListWidget#AutoDjQueueList::item:hover {
                background-color: #121B2A;
            }
            QListWidget#AutoDjQueueList::item:selected {
                background-color: #16263D;
                color: #00D4FF;
                border: 1px solid #00D4FF;
            }
            """
        )

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if (
            event.source() == self
            or mime.hasUrls()
            or mime.hasFormat("application/x-qabstractitemmodeldatalist")
        ):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if (
            event.source() == self
            or mime.hasUrls()
            or mime.hasFormat("application/x-qabstractitemmodeldatalist")
        ):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.source() == self:
            super().dropEvent(event)
            self.autodj.sync_queue_from_list_order()
            event.acceptProposedAction()
            return

        mime = event.mimeData()
        # 1. Archivos externos soltados desde el explorador de archivos
        if mime.hasUrls():
            paths = []
            for u in mime.urls():
                if u.isLocalFile():
                    p = u.toLocalFile()
                    ext = os.path.splitext(p)[1].lower()
                    if ext in AUDIO_EXTENSIONS:
                        paths.append(p)
            if paths:
                self.autodj.add_files(paths)
                event.acceptProposedAction()
                return

        # 2. Cues arrastrados desde la tabla principal de Linux Show Player
        cues = self.autodj.get_selected_layout_cues()
        if cues:
            self.autodj.add_cues(cues)
            event.acceptProposedAction()
            return

        super().dropEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            row = self.currentRow()
            if row >= 0:
                self.autodj.remove_item(row)
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_item_double_clicked(self, item):
        row = self.row(item)
        if 0 <= row < len(self.autodj.queue):
            self.autodj.play_index(row, crossfade=False)

    def _open_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        row = self.row(item)
        if not (0 <= row < len(self.autodj.queue)):
            return

        menu = QMenu(self)
        act_play = menu.addAction("▶ Reproducir este tema ahora")
        act_play.triggered.connect(lambda: self.autodj.play_index(row, crossfade=False))

        menu.addSeparator()
        if row > 0:
            act_up = menu.addAction("▲ Mover arriba")
            act_up.triggered.connect(lambda: self.autodj.move_item(row, -1))
        if row < len(self.autodj.queue) - 1:
            act_down = menu.addAction("▼ Mover abajo")
            act_down.triggered.connect(lambda: self.autodj.move_item(row, 1))

        menu.addSeparator()
        act_del = menu.addAction("✕ Eliminar de la cola")
        act_del.triggered.connect(lambda: self.autodj.remove_item(row))

        act_clear = menu.addAction("✕ Limpiar toda la cola")
        act_clear.triggered.connect(self.autodj.clear_queue)

        menu.exec_(self.mapToGlobal(pos))


class AutoDjWidget(QFrame):
    """Panel de Playlist Continua / AutoDJ que se inserta en InfoPanel."""

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.app = plugin.app
        self.setObjectName("AutoDjPanel")

        self.queue = []  # Lista de dicts: {"cue": cue, "id": id, "name": name, "dur_ms": int}
        self.current_index = -1
        self.current_cue = None
        self.is_active = False
        self.transition_triggered = False

        self._connected_cues = set()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            """
            QFrame#AutoDjPanel {
                background-color: #0A0E17;
                border: 1px solid #1C2638;
                border-radius: 4px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        # 1. Barra de herramientas superior compacta
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(3)

        self.btnAuto = QPushButton("▶ AUTO: OFF", self)
        self.btnAuto.setCheckable(True)
        self.btnAuto.setFixedHeight(22)
        self.btnAuto.setFixedWidth(98)
        self.btnAuto.setToolTip(
            "Activar / Desactivar reproducción continua automática sin pausas entre temas"
        )
        self._update_btn_auto_style(False)
        self.btnAuto.toggled.connect(self._on_auto_toggled)
        toolbar.addWidget(self.btnAuto)

        self.comboXfade = QComboBox(self)
        self.comboXfade.setFixedHeight(22)
        self.comboXfade.setFixedWidth(112)
        self.comboXfade.setToolTip("Tipo de empalme: cero cortes directo o crossfade suave")
        self.comboXfade.addItem("0s (Cero cortes)", 0.0)
        self.comboXfade.addItem("2s (Crossfade)", 2.0)
        self.comboXfade.addItem("3s (Crossfade)", 3.0)
        self.comboXfade.addItem("5s (Radio Mix)", 5.0)

        # Cargar preferencia guardada
        saved_xfade = float(self.plugin.Config.get("crossfade_sec", 3.0))
        idx = self.comboXfade.findData(saved_xfade)
        if idx >= 0:
            self.comboXfade.setCurrentIndex(idx)
        else:
            self.comboXfade.setCurrentIndex(2)
        self.comboXfade.currentIndexChanged.connect(self._on_xfade_changed)
        self.comboXfade.setStyleSheet(
            """
            QComboBox {
                background-color: #111724; border: 1px solid #243248; border-radius: 3px;
                color: #00D4FF; font-size: 8pt; font-weight: bold; padding: 1px 4px;
            }
            QComboBox::drop-down { width: 14px; border: none; }
            """
        )
        toolbar.addWidget(self.comboXfade)

        self.btnLoop = QPushButton("BUCLE", self)
        self.btnLoop.setCheckable(True)
        self.btnLoop.setFixedHeight(22)
        self.btnLoop.setFixedWidth(52)
        self.btnLoop.setToolTip("Bucle continuo: al terminar el último tema, reinicia desde el primero")
        loop_cfg = bool(self.plugin.Config.get("loop", False))
        self.btnLoop.setChecked(loop_cfg)
        self._update_btn_loop_style(loop_cfg)
        self.btnLoop.toggled.connect(self._on_loop_toggled)
        toolbar.addWidget(self.btnLoop)

        self.btnNext = QPushButton(">>", self)
        self.btnNext.setFixedHeight(22)
        self.btnNext.setFixedWidth(30)
        self.btnNext.setToolTip("Saltar inmediatamente al siguiente tema con mezcla")
        self.btnNext.setStyleSheet(
            "QPushButton { background-color: #141C2B; border: 1px solid #283954; color: #FFFFFF; font-weight: bold; font-size: 8pt; border-radius: 3px; padding: 1px 2px; }"
            "QPushButton:hover { background-color: #1E2B42; border-color: #00D4FF; }"
        )
        self.btnNext.clicked.connect(self.skip_next)
        toolbar.addWidget(self.btnNext)

        self.btnStop = QPushButton("STOP", self)
        self.btnStop.setFixedHeight(22)
        self.btnStop.setFixedWidth(44)
        self.btnStop.setToolTip("Detener reproducción continua")
        self.btnStop.setStyleSheet(
            "QPushButton { background-color: #241419; border: 1px solid #6E1A27; color: #FF6B81; font-weight: bold; font-size: 8pt; border-radius: 3px; padding: 1px 2px; }"
            "QPushButton:hover { background-color: #381B24; border-color: #FF2A4B; color: #FFFFFF; }"
        )
        self.btnStop.clicked.connect(self.stop_autodj)
        toolbar.addWidget(self.btnStop)

        self.btnAddSel = QPushButton("+ SEL.", self)
        self.btnAddSel.setFixedHeight(22)
        self.btnAddSel.setFixedWidth(52)
        self.btnAddSel.setToolTip("Añadir a la cola el cue seleccionado actualmente en la tabla manual")
        self.btnAddSel.setStyleSheet(
            "QPushButton { background-color: #0D263B; border: 1px solid #0099CC; color: #00D4FF; font-weight: bold; font-size: 8pt; border-radius: 3px; padding: 1px 3px; }"
            "QPushButton:hover { background-color: #113654; border-color: #00FFFF; color: #FFFFFF; }"
        )
        self.btnAddSel.clicked.connect(self.add_selected_cue)
        toolbar.addWidget(self.btnAddSel)

        self.btnClear = QPushButton("LIMPIAR", self)
        self.btnClear.setFixedHeight(22)
        self.btnClear.setFixedWidth(58)
        self.btnClear.setToolTip("Vaciar toda la cola de reproducción continua")
        self.btnClear.setStyleSheet(
            "QPushButton { background-color: #141C2B; border: 1px solid #283954; color: #8A9BB5; font-size: 7.5pt; font-weight: bold; border-radius: 3px; padding: 1px 2px; }"
            "QPushButton:hover { background-color: #2D141A; border-color: #FF2A4B; color: #FF6B81; }"
        )
        self.btnClear.clicked.connect(self.clear_queue)
        toolbar.addWidget(self.btnClear)

        self.lblStatus = QLabel("Cola: 0 temas (arrastra aquí)", self)
        self.lblStatus.setStyleSheet("font-size: 8pt; color: #7B8CA3; padding-left: 3px;")
        toolbar.addWidget(self.lblStatus)
        toolbar.addStretch(1)

        layout.addLayout(toolbar)

        # 2. Lista de temas en cola
        self.list_widget = AutoDjListWidget(self, self)
        layout.addWidget(self.list_widget)

        # 3. Timer de sincronización (100 ms) para tiempo restante y crossfade
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self.refresh_list_ui()

    def _update_btn_auto_style(self, active):
        if active:
            self.btnAuto.setText("● AUTO: ACTIVO")
            self.btnAuto.setStyleSheet(
                "QPushButton { background-color: #0A3D22; border: 1px solid #00E676; color: #00E676; font-weight: bold; font-size: 8pt; border-radius: 3px; padding: 1px 6px; }"
                "QPushButton:hover { background-color: #0E5430; }"
            )
        else:
            self.btnAuto.setText("▶ AUTO: OFF")
            self.btnAuto.setStyleSheet(
                "QPushButton { background-color: #131A26; border: 1px solid #26344A; color: #7D8FA8; font-size: 8pt; border-radius: 3px; padding: 1px 6px; }"
                "QPushButton:hover { border-color: #00D4FF; color: #FFFFFF; }"
            )

    def _update_btn_loop_style(self, loop):
        if loop:
            self.btnLoop.setStyleSheet(
                "QPushButton { background-color: #382408; border: 1px solid #FFB300; color: #FFB300; font-weight: bold; font-size: 8pt; border-radius: 3px; }"
            )
        else:
            self.btnLoop.setStyleSheet(
                "QPushButton { background-color: #141C2B; border: 1px solid #283954; color: #7D8FA8; font-size: 8pt; border-radius: 3px; }"
                "QPushButton:hover { border-color: #FFB300; color: #FFFFFF; }"
            )

    def _on_auto_toggled(self, checked):
        self._update_btn_auto_style(checked)
        self.is_active = checked
        if checked:
            if not self.queue:
                self.lblStatus.setText("Cola vacía. Arrastra canciones o pulsa ➕ Sel.")
                self.btnAuto.blockSignals(True)
                self.btnAuto.setChecked(False)
                self._update_btn_auto_style(False)
                self.btnAuto.blockSignals(False)
                self.is_active = False
                return
            if self.current_index < 0 or self.current_cue is None:
                start_row = max(0, self.list_widget.currentRow())
                self.play_index(start_row, crossfade=False)
        else:
            self.lblStatus.setText("AutoDJ Pausado (el tema sonando terminará normalmente)")

    def _on_loop_toggled(self, checked):
        self._update_btn_loop_style(checked)
        self.plugin.Config["loop"] = bool(checked)
        self.plugin.Config.write()

    def _on_xfade_changed(self, idx):
        val = float(self.comboXfade.currentData() or 0.0)
        self.plugin.Config["crossfade_sec"] = val
        self.plugin.Config.write()

    def get_crossfade_sec(self):
        val = self.comboXfade.currentData()
        return float(val) if val is not None else 3.0

    def get_selected_layout_cues(self):
        if self.app.session is None or self.app.layout is None:
            return []
        cues = list(self.app.layout.selected_cues())
        if not cues:
            standby = getattr(self.app.layout, "standby_cue", lambda: None)()
            if standby is not None:
                cues.append(standby)
        return cues

    def add_cue(self, cue):
        if cue is None:
            return
        cue_id = getattr(cue, "id", str(id(cue)))
        name = getattr(cue, "name", "Sin título") or "Sin título"
        dur_raw = getattr(cue, "duration", 0)
        dur_ms = dur_raw() if callable(dur_raw) else (dur_raw or 0)

        self.queue.append(
            {
                "cue": cue,
                "id": cue_id,
                "name": name,
                "dur_ms": dur_ms,
            }
        )
        self.refresh_list_ui()
        if self.is_active and self.current_index < 0:
            self.play_index(0, crossfade=False)

    def add_cues(self, cues):
        for c in cues:
            if c is not None:
                cue_id = getattr(c, "id", str(id(c)))
                name = getattr(c, "name", "Sin título") or "Sin título"
                dur_raw = getattr(c, "duration", 0)
                dur_ms = dur_raw() if callable(dur_raw) else (dur_raw or 0)
                self.queue.append(
                    {
                        "cue": c,
                        "id": cue_id,
                        "name": name,
                        "dur_ms": dur_ms,
                    }
                )
        self.refresh_list_ui()
        if self.is_active and self.current_index < 0 and self.queue:
            self.play_index(0, crossfade=False)

    def add_files(self, paths):
        for p in paths:
            cue = self._ensure_cue_for_file(p)
            if cue:
                self.add_cue(cue)

    def _ensure_cue_for_file(self, filepath):
        if self.app.session is None or self.app.layout is None:
            return None
        # Si ya existe en la sesión, reusarlo
        try:
            for cue in self.app.layout.cues(MediaCue):
                if hasattr(cue, "uri") and cue.uri:
                    path = getattr(cue.uri, "absolute_path", None) or getattr(
                        cue.uri, "path", None
                    )
                    if path and os.path.abspath(path) == os.path.abspath(filepath):
                        return cue
        except Exception:
            pass

        # Crear nuevo cue en la sesión
        try:
            from lisp.command.layout import LayoutAutoInsertCuesCommand
            from lisp.plugins.gst_backend.gst_backend import GstBackend
            from lisp.plugins.gst_backend.gst_media_cue import UriAudioCueFactory

            factory = UriAudioCueFactory(GstBackend.Config["pipeline"])
            cue = factory(self.app, uri=filepath)
            cue.name = os.path.splitext(os.path.basename(filepath))[0]
            self.app.commands_stack.do(
                LayoutAutoInsertCuesCommand(self.app.session.layout, cue)
            )
            return cue
        except Exception as e:
            logger.error("No se pudo crear cue para archivo %s: %e", filepath, e)
            return None

    def add_selected_cue(self):
        cues = self.get_selected_layout_cues()
        if cues:
            self.add_cues(cues)
        else:
            self.lblStatus.setText("Selecciona un tema en la tabla manual primero.")

    def remove_item(self, row):
        if not (0 <= row < len(self.queue)):
            return
        is_current = row == self.current_index
        removed = self.queue.pop(row)
        if is_current:
            if self.current_cue:
                try:
                    self.current_cue.stop()
                except Exception:
                    pass
                self.current_cue = None
            if self.is_active and self.queue:
                next_row = min(row, len(self.queue) - 1)
                self.play_index(next_row, crossfade=False)
            else:
                self.stop_autodj()
        elif row < self.current_index:
            self.current_index -= 1

        self.refresh_list_ui()

    def move_item(self, row, direction):
        target = row + direction
        if not (0 <= row < len(self.queue) and 0 <= target < len(self.queue)):
            return
        item = self.queue.pop(row)
        self.queue.insert(target, item)
        if self.current_index == row:
            self.current_index = target
        elif self.current_index == target:
            self.current_index = row
        self.refresh_list_ui()
        self.list_widget.setCurrentRow(target)

    def sync_queue_from_list_order(self):
        """Reconstruye self.queue tras un reordenamiento interno por Drag & Drop."""
        new_queue = []
        new_current = -1
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            data = it.data(Qt.UserRole)
            if data and "cue" in data:
                if self.current_cue and data.get("id") == getattr(
                    self.current_cue, "id", None
                ):
                    new_current = len(new_queue)
                new_queue.append(data)
        self.queue = new_queue
        self.current_index = new_current
        self.refresh_list_ui()

    def clear_queue(self):
        self.stop_autodj()
        self.queue.clear()
        self.refresh_list_ui()

    def play_index(self, index, crossfade=False):
        if not (0 <= index < len(self.queue)):
            return

        old_cue = self.current_cue
        xfade_sec = self.get_crossfade_sec()

        if old_cue is not None:
            try:
                if crossfade and xfade_sec > 0:
                    old_cue.fadeout(xfade_sec, FadeOutType.Linear)
                    # Programar parada limpia de la pista vieja cuando su volumen llegue a 0
                    # para liberar el pipeline de audio y restaurar su volumen nominal
                    stop_delay_ms = int((xfade_sec + 0.3) * 1000)
                    QTimer.singleShot(
                        stop_delay_ms,
                        lambda c=old_cue: c.stop()
                        if (c is not None and c is not self.current_cue)
                        else None,
                    )
                else:
                    old_cue.stop()
            except Exception as e:
                logger.warning("Error deteniendo cue anterior: %s", e)

        self.current_index = index
        data = self.queue[index]
        cue = data["cue"]
        self.current_cue = cue
        self.transition_triggered = False

        # Conectar señales de fin de pista
        self._hook_cue_signals(cue)

        # Asegurar volumen nominal en la nueva pista si estaba en 0 por fade previo
        try:
            vol = getattr(cue, "media", None) and cue.media.element("Volume")
            if vol is not None and not (crossfade and xfade_sec > 0):
                vol.live_volume = vol.volume
        except Exception:
            pass

        try:
            cue.start()
            if crossfade and xfade_sec > 0:
                def _do_fadein(c=cue, dur=xfade_sec):
                    try:
                        if c is self.current_cue:
                            c.fadein(dur, FadeInType.Linear)
                    except Exception:
                        pass

                QTimer.singleShot(25, _do_fadein)
        except Exception as e:
            logger.error("Error al iniciar cue en AutoDJ: %s", e)

        self.is_active = True
        self.btnAuto.blockSignals(True)
        self.btnAuto.setChecked(True)
        self._update_btn_auto_style(True)
        self.btnAuto.blockSignals(False)

        self._timer.start(100)
        self.refresh_list_ui()

    def _hook_cue_signals(self, cue):
        cue_id = getattr(cue, "id", None)
        if cue_id in self._connected_cues:
            return
        try:
            if hasattr(cue, "end"):
                cue.end.connect(self._on_cue_end)
            if hasattr(cue, "stopped"):
                cue.stopped.connect(self._on_cue_stopped)
            self._connected_cues.add(cue_id)
        except Exception as e:
            logger.debug("Error conectando señales a cue: %s", e)

    def _on_cue_end(self, cue=None, *args, **kwargs):
        if not self.is_active or self.current_cue is None:
            return

        # FILTRADO ESTRICTO: si la señal 'end' proviene de un cue saliente que terminó
        # su fadeout o llegó al final de archivo en segundo plano, IGNORARLA:
        if cue is not None and getattr(cue, "id", None) != getattr(
            self.current_cue, "id", None
        ):
            logger.debug(
                "AutoDJ: ignorando señal 'end' de cue saliente %s",
                getattr(cue, "name", ""),
            )
            return

        # Solo avanzar si realmente finalizó el cue marcado como activo actualmente
        if not self.transition_triggered:
            self.transition_triggered = True
            QTimer.singleShot(30, lambda: self.advance_next(crossfade=False))

    def _on_cue_stopped(self, cue=None, *args, **kwargs):
        pass

    def advance_next(self, crossfade=True):
        if not self.queue:
            self.stop_autodj()
            return

        next_idx = self.current_index + 1
        if next_idx >= len(self.queue):
            if self.btnLoop.isChecked():
                next_idx = 0
            else:
                self.stop_autodj()
                self.lblStatus.setText("Fin de la lista alcanzado.")
                return

        self.play_index(next_idx, crossfade=crossfade)

    def skip_next(self):
        if not self.queue:
            return
        if self.current_cue and self.is_active:
            self.advance_next(crossfade=True)
        else:
            self.play_index(max(0, self.current_index + 1))

    def stop_autodj(self):
        self.is_active = False
        self._timer.stop()
        if self.current_cue is not None:
            try:
                self.current_cue.stop()
            except Exception:
                pass
            self.current_cue = None
        self.current_index = -1
        self.transition_triggered = False
        self.btnAuto.blockSignals(True)
        self.btnAuto.setChecked(False)
        self._update_btn_auto_style(False)
        self.btnAuto.blockSignals(False)
        self.refresh_list_ui()
        self.lblStatus.setText("AutoDJ detenido.")

    def _tick(self):
        if not self.is_active or self.current_cue is None:
            return

        cur_raw = getattr(self.current_cue, "current_time", 0)
        try:
            cur = cur_raw() if callable(cur_raw) else (cur_raw or 0)
        except Exception:
            cur = 0

        dur_raw = getattr(self.current_cue, "duration", 0)
        try:
            dur = dur_raw() if callable(dur_raw) else (dur_raw or 0)
        except Exception:
            dur = 0

        name = getattr(self.current_cue, "name", "Sin título")

        cur_str = format_time_ms(cur)
        dur_str = format_time_ms(dur)

        if dur > 0:
            rem = max(0, dur - cur)
            rem_str = format_time_ms(rem)
            self.lblStatus.setText(
                f"♫ [{self.current_index + 1}/{len(self.queue)}] {name} "
                f"[{cur_str} / {dur_str} - resta {rem_str}]"
            )

            # Verificación de Crossfade
            xfade_ms = int(self.get_crossfade_sec() * 1000)
            if xfade_ms > 0 and dur > (xfade_ms * 2):
                if rem <= xfade_ms and not self.transition_triggered:
                    self.transition_triggered = True
                    self.advance_next(crossfade=True)
        else:
            self.lblStatus.setText(
                f"♫ [{self.current_index + 1}/{len(self.queue)}] {name} [{cur_str}]"
            )

        # Actualizar fila activa en la lista
        if 0 <= self.current_index < self.list_widget.count():
            item = self.list_widget.item(self.current_index)
            if item:
                badge = f"▶ [SONANDO - {cur_str} / {dur_str}]"
                item.setText(f"{badge}  {name}")

    def refresh_list_ui(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        if not self.queue:
            empty_item = QListWidgetItem(
                "♫ Arrastra temas aquí desde la lista o carpetas para reproducción continua"
            )
            empty_item.setFlags(Qt.NoItemFlags)
            empty_item.setForeground(QColor("#4A5C75"))
            self.list_widget.addItem(empty_item)
            self.lblStatus.setText("Cola: 0 temas (arrastra aquí)")
            self.list_widget.blockSignals(False)
            return

        for idx, item_data in enumerate(self.queue):
            cue = item_data["cue"]
            name = item_data.get("name") or getattr(cue, "name", "Sin título")
            dur = item_data.get("dur_ms") or getattr(cue, "duration", 0)
            dur_str = format_time_ms(dur) if dur > 0 else "--:--"

            is_playing = (
                self.is_active
                and idx == self.current_index
                and self.current_cue is not None
            )

            if is_playing:
                text = f"▶ [SONANDO]  {name}  ({dur_str})"
            else:
                text = f"[{idx + 1}] ♫ {name}  ({dur_str})"

            list_item = QListWidgetItem(text)
            list_item.setData(Qt.UserRole, item_data)

            if is_playing:
                list_item.setForeground(QColor("#00E676"))
                font = list_item.font()
                font.setBold(True)
                list_item.setFont(font)
            else:
                list_item.setForeground(QColor("#D6E2F0"))

            self.list_widget.addItem(list_item)

        if not self.is_active:
            self.lblStatus.setText(f"Cola: {len(self.queue)} temas en espera")

        self.list_widget.blockSignals(False)


class DjAutodj(Plugin):
    """Plugin de Playlist Continua / AutoDJ para Linux Show Player."""

    Name = "Playlist Continua AutoDJ"
    Authors = ("Personalización Arch/LiSP",)
    Description = (
        "Reproductor de música continua sin silencios (Zero-Gap / Crossfade) "
        "integrado en el recuadro central al lado de GO. Permite arrastrar "
        "canciones desde la lista manual o archivos de audio externos."
    )
    Version = "1.0.0"

    def __init__(self, app):
        super().__init__(app)
        self.autodj_widget = None
        self._attached = False
        self._target_panel = None
        self._target_desc = None

        # Acción en el menú contextual de cada Cue (clic derecho)
        self.menu_action = SimpleMenuAction(
            "+ Enviar a Playlist Continua",
            self._add_single_cue_from_menu,
            "+ Enviar cues a Playlist Continua",
            self._add_multiple_cues_from_menu,
            priority=MENU_PRIORITY_CUE + 5,
        )
        CueLayout.CuesMenu.add(self.menu_action, MediaCue)

        # Crear widget AutoDJ
        self.autodj_widget = AutoDjWidget(self)

        # Conectar creación de sesiones
        self.app.session_created.connect(self._on_session_created)

        # Intento inicial de montaje (en caso de que la sesión ya esté abierta)
        QTimer.singleShot(250, self._check_attach)

    def _on_session_created(self, session):
        QTimer.singleShot(300, self._check_attach)

    def _check_attach(self):
        if self._attached:
            return
        if self.app.session is None or self.app.layout is None:
            # Reintentar en 500 ms si el diálogo de inicio aún está abierto
            QTimer.singleShot(500, self._check_attach)
            return

        layout = self.app.layout
        if not hasattr(layout, "_view") or not hasattr(layout._view, "infoPanel"):
            return

        info_panel = layout._view.infoPanel
        if not hasattr(info_panel, "cueDescription"):
            return

        try:
            self._target_panel = info_panel
            self._target_desc = info_panel.cueDescription

            # Ocultar el recuadro negro vacío (cueDescription)
            self._target_desc.hide()

            # Insertar AutoDJ en su lugar
            info_panel.layout().addWidget(self.autodj_widget)
            self.autodj_widget.show()
            self._attached = True
            logger.info("AutoDJ widget adjuntado con éxito a InfoPanel.")
        except Exception as e:
            logger.error("Error al incrustar AutoDJ en InfoPanel: %s", e)

    def _add_single_cue_from_menu(self, cue):
        if self.autodj_widget:
            self.autodj_widget.add_cue(cue)

    def _add_multiple_cues_from_menu(self, cues):
        if self.autodj_widget:
            self.autodj_widget.add_cues(cues)

    def finalize(self):
        # Desregistrar acción de menú contextual
        try:
            CueLayout.CuesMenu.remove(self.menu_action, MediaCue)
        except Exception:
            pass

        # Desmontar widget y restaurar cueDescription
        if self._attached and self._target_panel and self._target_desc:
            try:
                self._target_panel.layout().removeWidget(self.autodj_widget)
                self.autodj_widget.setParent(None)
                self._target_desc.show()
            except Exception:
                pass
            self._attached = False

        if self.autodj_widget:
            self.autodj_widget.stop_autodj()

        super().finalize()
