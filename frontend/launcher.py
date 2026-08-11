"""Launcher do HUD: monta QGuiApplication + QQmlApplicationEngine e roda tudo
dentro de um único event loop unificado (Qt + asyncio), via
`PySide6.QtAsyncio` — a integração oficial do Qt for Python, sem servidor,
sem polling, sem loop recriado por mensagem.

Ver https://doc.qt.io/qtforpython-6/PySide6/QtAsyncio/index.html
"""

import logging
import sys
from pathlib import Path

import PySide6.QtAsyncio as QtAsyncio
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from app.application import JarvisApplication
from app.core import JarvisCore
from config.logging_config import configure_logging
from frontend.bridge import JarvisBridge

QML_DIR = Path(__file__).resolve().parent / "qml"

logger = logging.getLogger(__name__)


def _force_utf8_console() -> None:
    # Mesmo motivo do terminal (ver main.py): console do Windows corrompe
    # acentos do português sem isso.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def run() -> int:
    _force_utf8_console()
    configure_logging()

    qt_app = QGuiApplication(sys.argv)
    qt_app.setOrganizationName("JARVIS")
    qt_app.setApplicationName("JARVIS")
    qt_app.setQuitOnLastWindowClosed(True)

    core = JarvisCore()
    application = JarvisApplication(core)
    bridge = JarvisBridge(application, dev_mode=core.settings.dev_mode)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))

    if not engine.rootObjects():
        logger.error("Falha ao carregar a interface (Main.qml) — veja os erros de QML acima.")
        return 1

    async def _main() -> None:
        await bridge.start()

    QtAsyncio.run(_main(), keep_running=True, quit_qapp=True, handle_sigint=True)
    return 0
