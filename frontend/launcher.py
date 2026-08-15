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

from app.account_manager import AccountManager
from config.logging_config import configure_logging
from config.settings import settings
from frontend.bridge import JarvisBridge
from services.vosk_model_manager import VoiceModelManager

def _qml_dir() -> Path:
    """Diretório do QML, resolvido em RUNTIME e não no import.

    Rodando do código-fonte é `frontend/qml/` ao lado deste arquivo — o
    comportamento de sempre. Empacotado, `__file__` aponta para dentro do
    arquivo de bytecode do bundle, que não é um diretório de verdade; o
    caminho correto é relativo à raiz de recursos que o PyInstaller expõe
    (ver `config/paths.py::resource_root`).

    Resolver em runtime, e não numa constante de módulo, é o que permite
    `JARVIS_USER_DATA`/`sys._MEIPASS` valerem — uma constante seria
    congelada no momento do import, antes de o ambiente estar montado."""
    from config.paths import is_frozen, resource_root

    if is_frozen():
        return resource_root() / "frontend" / "qml"
    return Path(__file__).resolve().parent / "qml"


# Mantido como constante para os chamadores existentes (testes de smoke de
# QML importam este nome). Em desenvolvimento é exatamente o valor de antes.
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

    # `AccountManager` é dono do ciclo de vida de JarvisCore/JarvisApplication
    # por usuário logado — não existe mais uma sessão única global (v0.9).
    # `VoiceModelManager` é do dispositivo, não por conta (ver frontend/README.md,
    # seção Voz: "não baixe um modelo idêntico por usuário").
    account_manager = AccountManager(settings)
    voice_model_manager = VoiceModelManager(models_dir=settings.stt_models_dir)
    bridge = JarvisBridge(account_manager, voice_model_manager, dev_mode=settings.dev_mode)

    qml_dir = _qml_dir()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(qml_dir))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(qml_dir / "Main.qml")))

    if not engine.rootObjects():
        logger.error("Falha ao carregar a interface (Main.qml) — veja os erros de QML acima.")
        return 1

    async def _main() -> None:
        # Tenta continuar logado a partir de uma sessão local persistida;
        # se não houver, o HUD mostra a tela de login (bridge.authenticated
        # começa False) — login/registro reais acontecem por ação do
        # usuário, via os slots `login`/`register` do próprio Bridge.
        await bridge.initialize()

    QtAsyncio.run(_main(), keep_running=True, quit_qapp=True, handle_sigint=True)
    return 0
