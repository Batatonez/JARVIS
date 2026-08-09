"""Interface de terminal do JARVIS — a única camada de apresentação desta v0.1.

Fica fina de propósito: só lê entrada, mostra saída e trata encerramento.
Toda decisão real acontece em `JarvisCore`/`Orchestrator`, para que uma
futura interface gráfica (HUD) possa reutilizá-los sem duplicar lógica.
"""

import logging

from app.commands import JarvisExit
from app.core import JarvisCore

logger = logging.getLogger(__name__)

BANNER = """JARVIS
Core v0.1

Sistema iniciado.
Digite uma mensagem ou /help.
"""


def run(core: JarvisCore) -> None:
    print(BANNER)
    core.start()

    try:
        while True:
            try:
                line = input("> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            try:
                response = core.handle_input(line)
            except JarvisExit:
                break
            except Exception:
                logger.exception("Erro inesperado ao processar entrada: %r", line)
                print("\nJARVIS: Ocorreu um erro interno inesperado. Veja o log para mais detalhes.\n")
                continue

            if response:
                print()
                print(response)
                print()
    finally:
        core.stop()

    print("Encerrando JARVIS...")
