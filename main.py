"""Ponto de entrada único do JARVIS.

Uso:
    python main.py
"""

import asyncio
import sys

from app.application import JarvisApplication
from app.core import JarvisCore
from app.terminal import run
from config.logging_config import configure_logging


def _force_utf8_console() -> None:
    # No Windows, o codepage padrão do console (ex.: cp1252) corrompe acentos
    # do português. Forçar UTF-8 aqui garante saída correta independente da
    # configuração do terminal do usuário.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    _force_utf8_console()
    configure_logging()
    core = JarvisCore()
    application = JarvisApplication(core)
    # Um único event loop para toda a execução — não é recriado por mensagem.
    asyncio.run(run(application))


if __name__ == "__main__":
    main()
