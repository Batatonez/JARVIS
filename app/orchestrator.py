"""Camada de orquestração: decide o que fazer com cada entrada do usuário.

Nesta v0.1 a decisão é simples (comando interno vs. mensagem comum), mas é
aqui que futuramente entrarão as decisões de quando consultar memória, quando
chamar uma ferramenta, quando pedir confirmação, quando gerar eventos para o
HUD e quando usar subagentes (ver `docs/architecture.md`).
"""

import logging
from typing import TYPE_CHECKING

from app.commands import CommandRegistry
from app.state import JarvisState
from services.ai_service import AIServiceUnavailableError

if TYPE_CHECKING:
    from app.core import JarvisCore

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, core: "JarvisCore") -> None:
        self._core = core
        self._commands = CommandRegistry(core)

    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        self._core.event_bus.emit("message.received", text=text)

        if CommandRegistry.is_command(text):
            response = self._handle_command(text)
        else:
            response = self._handle_message(text)

        self._core.event_bus.emit("message.responded", text=response)
        return response

    def _handle_command(self, text: str) -> str:
        # Comandos internos são síncronos e instantâneos (introspecção ou
        # utilidades como /status e /clear) — não representam "trabalho" e
        # por isso não passam pelo estado WORKING, para não fazer o próprio
        # /status se ver preso no meio da própria transição de estado.
        response = self._commands.execute(text)
        self._core.event_bus.emit("command.executed", text=text)
        return response

    def _handle_message(self, text: str) -> str:
        self._core.set_state(JarvisState.THINKING)
        try:
            if not self._core.ai_service.is_available():
                reply = (
                    "O serviço de inteligência ainda não está conectado. "
                    "Digite /help para ver os comandos disponíveis."
                )
            else:
                try:
                    reply = self._core.ai_service.ask(text)
                except AIServiceUnavailableError as exc:
                    # Cobre tanto o placeholder quanto falhas em runtime de um
                    # provider real (ex.: erro de rede/API do ClaudeProvider).
                    # A mensagem já vem segura (sem segredos) do próprio serviço.
                    logger.warning("Falha ao obter resposta de IA: %s", exc)
                    reply = str(exc)
        finally:
            self._core.set_state(JarvisState.IDLE)
        return f"JARVIS: {reply}"
