import os
from PyQt5.QtGui import QColor, QPalette
from lisp.ui.themes.dark import assets as _dark_assets  # noqa: F401
from lisp.ui.themes.dark.dark import Dark


class DjConsoleNeon:
    QssPath = os.path.join(os.path.dirname(__file__), "theme.qss")

    def apply(self, qt_app):
        background = QColor(11, 14, 20)
        foreground = QColor(22, 27, 38)
        text = QColor(232, 240, 250)
        highlight = QColor(0, 212, 255)

        palette = qt_app.palette()
        palette.setColor(QPalette.Window, foreground)
        palette.setColor(QPalette.WindowText, text)
        palette.setColor(QPalette.Base, background)
        palette.setColor(QPalette.AlternateBase, foreground.darker(125))
        palette.setColor(QPalette.ToolTipBase, foreground)
        palette.setColor(QPalette.ToolTipText, text)
        palette.setColor(QPalette.Text, text)
        palette.setColor(QPalette.Button, foreground)
        palette.setColor(QPalette.ButtonText, text)
        palette.setColor(QPalette.BrightText, QColor(255, 65, 65))
        palette.setColor(QPalette.Link, highlight)

        palette.setColor(QPalette.Light, foreground.lighter(165))
        palette.setColor(QPalette.Midlight, foreground.lighter(130))
        palette.setColor(QPalette.Dark, foreground.darker(160))
        palette.setColor(QPalette.Mid, foreground.darker(130))

        palette.setColor(QPalette.Highlight, highlight)
        palette.setColor(QPalette.HighlightedText, QColor(5, 10, 15))

        qt_app.setPalette(palette)

        base_qss = ""
        if os.path.exists(Dark.QssPath):
            with open(Dark.QssPath, mode="r", encoding="utf-8") as bf:
                base_qss = bf.read()

        with open(self.QssPath, mode="r", encoding="utf-8") as f:
            custom_qss = f.read()

        qt_app.setStyleSheet(base_qss + "\n" + custom_qss)
