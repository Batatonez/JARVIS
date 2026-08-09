"""Abstração para o serviço de inteligência do JARVIS.

    AIService
        |
        UnavailableAIService   (placeholder: nenhuma API key configurada)
        ClaudeProvider          (services/claude_provider.py — API key configurada)

`create_ai_service()` decide qual das duas usar. Este módulo não importa o
SDK da Anthropic — só `services/claude_provider.py` faz isso, e só quando
`create_ai_service()` realmente precisa construir um `ClaudeProvider`.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


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


def create_ai_service(settings: "Settings") -> AIService:
    """Decide qual AIService usar a partir da configuração:

        API key configurada  -> ClaudeProvider
        API key ausente       -> UnavailableAIService

    Nunca levanta exceção: se a configuração existir mas o provider falhar
    ao ser construído, cai de volta para `UnavailableAIService` (fallback
    seguro) em vez de derrubar o JARVIS.
    """
    if not settings.has_anthropic_api_key():
        return UnavailableAIService()

    from services.claude_provider import ClaudeProvider  # import local: só quando usado

    try:
        return ClaudeProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            timeout=settings.anthropic_timeout,
            max_tokens=settings.anthropic_max_tokens,
        )
    except Exception:
        logger.exception("Falha ao inicializar o ClaudeProvider; usando UnavailableAIService.")
        return UnavailableAIService()
