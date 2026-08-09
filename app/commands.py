"""Comandos internos do JARVIS (`/help`, `/status`, `/memory`, `/clear`, `/exit`).

Cada comando recebe os argumentos após o nome (string, pode ser vazia) e
devolve o texto já formatado para exibição — a camada de apresentação
(`app/terminal.py`) apenas imprime o que vier daqui.
"""

import logging
import os
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.core import JarvisCore

logger = logging.getLogger(__name__)


class JarvisExit(Exception):
    """Sinaliza que o usuário pediu para encerrar o JARVIS (`/exit`, `/quit`)."""


class CommandRegistry:
    def __init__(self, core: "JarvisCore") -> None:
        self._core = core
        self._commands: dict[str, Callable[[str], str]] = {
            "help": self._cmd_help,
            "status": self._cmd_status,
            "memory": self._cmd_memory,
            "clear": self._cmd_clear,
            "exit": self._cmd_exit,
            "quit": self._cmd_exit,
        }

    @staticmethod
    def is_command(text: str) -> bool:
        return text.startswith("/")

    def execute(self, text: str) -> str:
        name, _, args = text[1:].partition(" ")
        handler = self._commands.get(name.strip().lower())
        if handler is None:
            return f"Comando desconhecido: /{name}. Digite /help para ver os comandos disponíveis."
        return handler(args.strip())

    def _cmd_help(self, args: str) -> str:
        return (
            "Comandos disponíveis:\n"
            "/help     Mostra esta lista de comandos.\n"
            "/status   Mostra o status atual do JARVIS Core.\n"
            "/memory   Mostra se profile.md e preferences.md estão carregados.\n"
            "/clear    Limpa a tela do terminal.\n"
            "/exit     Encerra o JARVIS (alias: /quit)."
        )

    def _cmd_status(self, args: str) -> str:
        core = self._core
        ai = core.ai_service
        memory_ok = (
            core.memory_service.is_profile_available()
            or core.memory_service.is_preferences_available()
        )
        return (
            "JARVIS Core\n"
            "Status: online\n"
            f"Versão: {core.settings.core_version}\n"
            f"Estado: {core.state.value}\n"
            f"Memória: {'disponível' if memory_ok else 'indisponível'}\n"
            f"IA: {'configurada' if ai.is_available() else 'não configurada'}\n"
            f"Backend de IA: {ai.backend_name}\n"
            f"Sessão: {'ativa' if ai.session_active else 'inativa'}"
        )

    def _cmd_memory(self, args: str) -> str:
        profile_ok = self._core.memory_service.is_profile_available()
        preferences_ok = self._core.memory_service.is_preferences_available()
        return (
            "Memória\n"
            f"profile.md: {'carregado' if profile_ok else 'não encontrado'}\n"
            f"preferences.md: {'carregado' if preferences_ok else 'não encontrado'}"
        )

    def _cmd_clear(self, args: str) -> str:
        os.system("cls" if os.name == "nt" else "clear")
        return ""

    def _cmd_exit(self, args: str) -> str:
        raise JarvisExit()
