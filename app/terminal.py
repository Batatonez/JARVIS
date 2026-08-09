"""Interface de terminal do JARVIS — a única camada de apresentação desta versão.

Fica fina de propósito: só lê entrada, mostra saída e trata encerramento.
Toda decisão real acontece em `JarvisCore`/`Orchestrator`, para que uma
futura interface gráfica (HUD) possa reutilizá-los sem duplicar lógica.

`run()` é `async` e roda inteira dentro do único event loop criado por
`main.py` (`asyncio.run`) — não criamos/destruímos um loop por mensagem.
`input()` é uma chamada bloqueante, mas como este loop não tem nenhuma outra
tarefa concorrente para atender enquanto espera o usuário digitar, isso não
é um problema nesta versão (ver `docs/architecture.md`, seção Async).
"""

import logging

from app.commands import JarvisExit
from app.core import JarvisCore

logger = logging.getLogger(__name__)

BANNER = """JARVIS
Core v0.3

Sistema iniciado.
Digite uma mensagem ou /help.
"""


async def run(core: JarvisCore) -> None:
    print(BANNER)
    await core.start()

    try:
        while True:
            try:
                line = input("> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            try:
                response = await core.handle_input(line)
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
        await core.stop()

    print("Encerrando JARVIS...")
