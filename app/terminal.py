"""Interface de terminal do JARVIS — o primeiro frontend da Application Layer.

Fica fina de propósito: só lê entrada, mostra saída e trata encerramento.
Toda decisão real acontece em `JarvisApplication` (que por sua vez delega a
`JarvisCore`/`Orchestrator`) — o terminal não conhece `JarvisCore`,
`Orchestrator`, serviços internos nem o Claude Agent SDK. Uma futura
interface gráfica (HUD) deve falar com o mesmo `JarvisApplication`.

`run()` é `async` e roda inteira dentro do único event loop criado por
`main.py` (`asyncio.run`) — não criamos/destruímos um loop por mensagem.
`input()` é uma chamada bloqueante, mas como este loop não tem nenhuma outra
tarefa concorrente para atender enquanto espera o usuário digitar, isso não
é um problema nesta versão (ver `docs/architecture.md`, seção Async).
"""

import logging

from app.application import JarvisApplication
from app.commands import JarvisExit

logger = logging.getLogger(__name__)

def build_banner(version: str) -> str:
    """A versão vem de `Settings.core_version` (fonte única) em vez de ser
    repetida aqui — na v1.1 este banner ficou preso em "v1.0" justamente
    por ser uma string solta que ninguém lembrou de atualizar."""
    return (
        "JARVIS\n"
        f"Core v{version}\n"
        "\n"
        "Sistema iniciado.\n"
        "Digite uma mensagem ou /help.\n"
    )


async def run(application: JarvisApplication) -> None:
    print(build_banner(application.get_status().core_version))
    await application.start()

    try:
        while True:
            try:
                line = input("> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            try:
                response = await application.handle_input(line)
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
        await application.stop()

    print("Encerrando JARVIS...")
