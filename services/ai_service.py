"""Abstração para o serviço de inteligência do JARVIS.

    AIService
        |
        UnavailableAIService   (placeholder: nenhuma API key configurada)
        ClaudeAgentProvider     (services/claude_agent_provider.py — API key configurada)

`create_ai_service()` decide qual das duas usar. Este módulo não importa o
Claude Agent SDK — só `services/claude_agent_provider.py` faz isso, e só
quando `create_ai_service()` realmente precisa construir um provider real.

O lifecycle é explícito (`start` conecta/reutiliza uma sessão, `ask` envia
uma mensagem dentro dela, `close` encerra) porque o Agent SDK mantém uma
sessão contínua de conversa — não recriamos uma sessão a cada mensagem.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class AIServiceUnavailableError(Exception):
    """Levantada quando não é possível obter uma resposta de IA."""


class AIService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Indica se há um provider de IA real configurado (não necessariamente conectado)."""

    @property
    @abstractmethod
    def session_active(self) -> bool:
        """Indica se existe uma sessão de conversa ativa neste momento."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Nome legível do backend em uso (para /status), ex.: 'Claude Agent SDK'."""

    @abstractmethod
    async def start(self, *, memory_context: str = "") -> None:
        """Conecta/inicia a sessão. Idempotente: chamar de novo não deve recriar a sessão."""

    @abstractmethod
    async def ask(self, message: str) -> str:
        """Envia uma mensagem na sessão ativa e retorna o texto da resposta."""

    @abstractmethod
    async def close(self) -> None:
        """Encerra a sessão, se houver uma. Idempotente."""


class UnavailableAIService(AIService):
    """Placeholder: nenhum provider de IA está conectado nesta etapa."""

    def is_available(self) -> bool:
        return False

    @property
    def session_active(self) -> bool:
        return False

    @property
    def backend_name(self) -> str:
        return "nenhum"

    async def start(self, *, memory_context: str = "") -> None:
        return None

    async def ask(self, message: str) -> str:
        raise AIServiceUnavailableError(
            "O serviço de inteligência ainda não está conectado."
        )

    async def close(self) -> None:
        return None


def create_ai_service(settings: "Settings") -> AIService:
    """Decide qual AIService usar a partir da configuração:

        OPENROUTER_API_KEY   -> ProviderRouterAIService (v1.0, via ProviderRouter)
        ANTHROPIC_API_KEY    -> ClaudeAgentProvider
        nenhuma das duas     -> UnavailableAIService

    OpenRouter tem precedência porque é o caminho que passa pelo
    `ProviderRouter` — a camada que dá ao JARVIS controle real de
    provider/modelo/custo (`free_only`), ver docs/providers.md. O
    ClaudeAgentProvider continua como estava para quem tiver uma chave
    Anthropic e nenhuma de OpenRouter.

    Nunca levanta exceção: se a configuração existir mas o provider falhar
    ao ser construído, cai de volta para `UnavailableAIService` (fallback
    seguro) em vez de derrubar o JARVIS. Nenhuma conexão é feita aqui —
    construir um provider não toca rede.
    """
    if settings.has_openrouter_api_key():
        try:
            from services.provider_ai_service import ProviderRouterAIService
            from services.providers.registry import build_default_registry
            from services.providers.router import ProviderRouter

            router = ProviderRouter(build_default_registry())
            return ProviderRouterAIService(
                router,
                free_only=settings.free_only,
                max_tokens=settings.provider_max_tokens,
                timeout_s=settings.provider_timeout_s,
            )
        except Exception:
            logger.exception("Falha ao inicializar o ProviderRouter; tentando os demais providers.")

    if not settings.has_anthropic_api_key():
        return UnavailableAIService()

    from services.claude_agent_provider import ClaudeAgentProvider  # import local: só quando usado

    try:
        return ClaudeAgentProvider(model=settings.agent_model, cwd=str(settings.project_root))
    except Exception:
        logger.exception("Falha ao inicializar o ClaudeAgentProvider; usando UnavailableAIService.")
        return UnavailableAIService()
