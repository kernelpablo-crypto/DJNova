# This file is part of Linux Show Player
#
# Copyright 2018 Francesco Ceruti <ceppofrancy@gmail.com>
#
# Linux Show Player is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Linux Show Player is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Linux Show Player.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import logging
import os
import shutil
import signal
import sys
from functools import partial
from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import QLocale, QLibraryInfo, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from lisp import app_dirs, DEFAULT_APP_CONFIG, USER_APP_CONFIG, plugins
from lisp.application import Application
from lisp.core.configuration import JSONFileConfiguration
from lisp.core.djnova_platform import get_asset_path
from lisp.ui import themes
from lisp.ui.icons import IconTheme
from lisp.ui.ui_utils import install_translation, PyQtUnixSignalHandler


def _seed_bundled_presets():
    """Copia presets DJ de fábrica en la primera ejecución del usuario."""
    src_presets = get_asset_path("presets")
    if not os.path.isdir(src_presets):
        return
    dst_presets = os.path.join(app_dirs.user_data_dir, "presets")
    os.makedirs(dst_presets, exist_ok=True)
    for name in os.listdir(src_presets):
        src = os.path.join(src_presets, name)
        dst = os.path.join(dst_presets, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def main():
    # Parse the command-line arguments
    parser = argparse.ArgumentParser(
        description="DJNova - Consola DJ, AutoDJ, Escenario Video y Broadcast."
    )
    parser.add_argument(
        "-f",
        "--file",
        default="",
        nargs="?",
        const="",
        help="Session file to open",
    )
    parser.add_argument(
        "-l",
        "--log",
        choices=["debug", "info", "warning"],
        default="warning",
        help="Change output verbosity. default: warning",
    )
    parser.add_argument(
        "--locale", default="", help="Force specified locale/language"
    )

    args = parser.parse_args()

    # Make sure the application user directories exist
    os.makedirs(app_dirs.user_config_dir, exist_ok=True)
    os.makedirs(app_dirs.user_data_dir, exist_ok=True)
    _seed_bundled_presets()

    # Get logging level for the console
    if args.log == "debug":
        console_log_level = logging.DEBUG
        # If something bad happen at low-level (e.g. segfault) print the stack
        import faulthandler

        faulthandler.enable()
    elif args.log == "info":
        console_log_level = logging.INFO
    else:
        console_log_level = logging.WARNING

    # Setup the root logger
    default_formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d\t%(name)s\t%(levelname)s\t%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Create the console handler
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(default_formatter)
    stream_handler.setLevel(console_log_level)
    root_logger.addHandler(stream_handler)

    # Make sure the logs directory exists
    os.makedirs(app_dirs.user_log_dir, exist_ok=True)
    # Create the file handler
    file_handler = RotatingFileHandler(
        os.path.join(app_dirs.user_log_dir, "djnova.log"),
        maxBytes=10 * (2**20),
        backupCount=5,
    )
    file_handler.setFormatter(default_formatter)
    root_logger.addHandler(file_handler)

    # Load application configuration
    app_conf = JSONFileConfiguration(USER_APP_CONFIG, DEFAULT_APP_CONFIG)

    # Create the QApplication
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("DJNova")
    qt_app.setOrganizationName("DJNova")
    qt_app.setQuitOnLastWindowClosed(True)

    # Get/Set the locale
    if args.locale:
        qt_locale = QLocale(args.locale)
    elif app_conf["locale"]:
        qt_locale = QLocale(app_conf["locale"])
    else:
        qt_locale = QLocale()

    QLocale.setDefault(qt_locale)
    logging.info(
        f'Using "{qt_locale.name()}" locale -> {qt_locale.uiLanguages()}'
    )

    # Qt platform translation
    qt_tr_path = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    # install_translation("qt", tr_path=qt_tr_path)
    install_translation("qtbase", tr_path=qt_tr_path)
    # Main app translations
    install_translation("base")

    # Set UI theme
    try:
        theme_name = app_conf["theme.theme"]
        themes.get_theme(theme_name).apply(qt_app)
        logging.info(f'Using "{theme_name}" theme')
    except Exception:
        logging.exception("Unable to load theme.")

    # Set LiSP icon theme
    try:
        icon_theme = app_conf["theme.icons"]
        IconTheme.set_theme_name(icon_theme)
        logging.info(f'Using "{icon_theme}" icon theme')
    except Exception:
        logging.exception("Unable to load icon theme.")
    else:
        # Set application icon (DJNova logo)
        logo_png = get_asset_path("logo", "djnova-128.png")
        if os.path.exists(logo_png):
            qt_app.setWindowIcon(QIcon(logo_png))
        else:
            qt_app.setWindowIcon(
                QIcon(IconTheme.get("djnova").pixmap(128, 128))
            )

    # Initialize the application
    lisp_app = Application(app_conf)
    plugins.load_plugins(lisp_app)

    # Handle SIGTERM and SIGINT by quitting the QApplication
    def handle_quit_signal(*_):
        qt_app.quit()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_quit_signal)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, handle_quit_signal)

    with PyQtUnixSignalHandler():
        # Defer application start when QT main-loop starts
        QTimer.singleShot(0, partial(lisp_app.start, session_file=args.file))
        # Start QT main-loop, blocks until exit
        exit_code = qt_app.exec()

        # Finalize all and exit
        plugins.finalize_plugins()
        lisp_app.finalize()

    sys.exit(exit_code)


def _report_fatal_error(exc):
    """Registra errores fatales de arranque y muestra aviso visible en GUI."""
    import traceback

    err_text = traceback.format_exc()
    logging.critical("Error fatal al iniciar DJNova:\n%s", err_text)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    log_file = os.path.join(app_dirs.user_log_dir, "djnova.log")
    msg = f"No se pudo iniciar DJNova:\n{exc}\n\nRegistro:\n{log_file}"
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, msg, "DJNova - Error de inicio", 0x10)
            return
        except Exception:
            pass
    try:
        from PyQt5.QtWidgets import QMessageBox

        if QApplication.instance() is None:
            QApplication(sys.argv[:1])
        QMessageBox.critical(None, "DJNova - Error de inicio", msg)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _report_fatal_error(exc)
        sys.exit(1)
