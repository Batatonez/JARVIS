"""Abstração para o serviço de inteligência do JARVIS.

Nesta versão nenhum provider real está conectado — `UnavailableAIService` é
o placeholder usado até que um provider (ex.: Claude, via `ClaudeProvider`)
seja implementado em uma etapa futura:

    AIService
        |
        UnavailableAIService   (esta versão)
        ClaudeProvider          (futuro)
"""

from abc import ABC, abstractmethod


class AIServiceUnavailableError(Exception):
    """Levantada ao pedir uma resposta de IA sem um provider conectado."""


class AIService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Indica se há um provider de IA real conectado."""

    @abstractmethod
    def ask(self, message: str) -> str:
        """Envia uma mensagem ao provider de IA e retorna a resposta."""


class UnavailableAIService(AIService):
    """Placeholder: nenhum provider de IA está conectado nesta etapa."""

    def is_available(self) -> bool:
        return False

    def ask(self, message: str) -> str:
        raise AIServiceUnavailableError(
            "O serviço de inteligência ainda não está conectado."
        )
