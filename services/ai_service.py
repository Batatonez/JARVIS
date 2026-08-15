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

    # --- v1.3: requisição isolada -------------------------------------
    # Concretos (não abstratos) para não quebrar nenhuma implementação
    # existente nem fake de teste.

    @property
    def supports_isolated_requests(self) -> bool:
        """`True` quando o provider consegue responder uma pergunta SEM
        entrar no histórico da conversa."""
        return False

    async def ask_isolated(self, prompt: str, *, max_tokens: int = 64) -> str:
        """Uma pergunta avulsa que **não** toca a sessão do usuário.

        Existe para tarefas auxiliares — hoje só o título automático de chat
        (`services/chat_title_service.py`). Sem isto, gerar um título com
        `ask()` injetaria o prompt do título no histórico da conversa e a IA
        passaria a responder considerando aquilo, o que é visível para o
        usuário e simplesmente errado."""
        raise AIServiceUnavailableError(
            "Este provider não suporta requisições isoladas."
        )


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

        qualquer provider do Provider Router configurado
                              -> ProviderRouterAIService (v1.0+, via ProviderRouter)
        ANTHROPIC_API_KEY     -> ClaudeAgentProvider
        nenhum dos dois       -> UnavailableAIService

    **v1.4.0**: "qualquer provider configurado" deixou de ser só
    `OPENROUTER_API_KEY` — o registry sempre inclui OpenRouter, NVIDIA,
    Gemini, Groq, Cerebras e Mistral (cada um decide sozinho se está
    configurado; ver `services/providers/registry.py::build_default_registry`),
    então uma conta que só tenha `NVIDIA_API_KEY`, por exemplo, já basta para
    o JARVIS conversar de verdade pelo Provider Router. A construção do
    registry não toca rede — só checar `configured_provider_ids()` depois é
    que revela se sobrou algum candidato.

    O ClaudeAgentProvider continua como estava para quem tiver uma chave
    Anthropic e nenhuma das seis do Provider Router.

    Nunca levanta exceção: se a configuração existir mas o provider falhar
    ao ser construído, cai de volta para `UnavailableAIService` (fallback
    seguro) em vez de derrubar o JARVIS.
    """
    try:
        from services.provider_ai_service import ProviderRouterAIService
        from services.providers.registry import build_default_registry
        from services.providers.router import ProviderRouter

        router = ProviderRouter(build_default_registry())
        if router.configured_provider_ids():
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
