"""Tipos normalizados do Provider Router — o JARVIS nunca fica acoplado ao
formato particular de um provider (OpenRouter, Groq, Gemini, ...): todo
provider concreto traduz sua resposta nativa para `AIExecutionResult` antes
de devolver ao chamador.

Distinção deliberada entre `requested_model` (o que pedimos) e
`served_model`/`cost` (o que o provider de fato relata ter usado/cobrado) —
ver `docs/providers.md`, seção "free-only". Nunca presuma custo zero só
porque o nome do modelo contém "free".
"""

from dataclasses import dataclass, field
from enum import Enum

# Slug completo de um modelo, no formato que o próprio provider espera
# receber de volta (ex.: "openrouter/free", "claude-sonnet-5"). Não é uma
# classe própria de propósito — todo provider já fala nesse formato nativamente.
ModelId = str


class ProviderId(str, Enum):
    OPENROUTER = "openrouter"
    GROQ = "groq"
    GEMINI = "gemini"
    MISTRAL = "mistral"
    NVIDIA = "nvidia"
    ANTHROPIC = "anthropic"


class ProviderStatus(str, Enum):
    NOT_IMPLEMENTED = "not_implemented"  # sem classe de provider ainda (Groq, Gemini, ...)
    NOT_CONFIGURED = "not_configured"  # provider existe, mas sem API key no ambiente
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"  # configurado, mas indisponível agora (erro de rede, rate limit, etc.)


class ModelCapability(str, Enum):
    CHAT = "chat"
    TOOLS = "tools"
    STREAMING = "streaming"
    EMBEDDINGS = "embeddings"


@dataclass(frozen=True)
class ProviderDescriptor:
    """O que o registry sabe sobre um provider, sem precisar instanciá-lo
    nem tocar rede — usado pelo HUD/CLI para mostrar `OpenRouter: configured`
    sem nunca expor a chave em si (ver `services/providers/secrets.py`)."""

    id: ProviderId
    status: ProviderStatus
    supports_chat: bool = False
    supports_tools: bool = False
    supports_streaming: bool = False
    supports_embeddings: bool = False
    free_models: tuple[ModelId, ...] = ()


@dataclass(frozen=True)
class RouteRequest:
    prompt: str
    system_prompt: str | None = None
    # Histórico da conversa ANTES de `prompt`, como pares (role, content) com
    # role em {"user", "assistant"}. Existe porque a API da OpenRouter é
    # stateless: ao contrário do Claude Agent SDK (que mantém a sessão do
    # lado dele), aqui o contexto precisa ser reenviado a cada chamada.
    # Tupla (não lista) para manter o dataclass congelado/hashável.
    history: tuple[tuple[str, str], ...] = ()
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_s: float | None = None
    # Quando True: o ProviderRouter nunca cai para um modelo pago, nem
    # silenciosamente nem em erro — recusa explicitamente
    # (`NoFreeModelAvailableError`, código `NO_FREE_MODEL_AVAILABLE`) em vez
    # de arriscar gastar dinheiro. Ver docs/providers.md, seção free-only.
    free_only: bool = False
    preferred_provider: ProviderId | None = None
    # Ex.: "openrouter/free" — um pedido explícito de modelo/rota. O router
    # NUNCA aceita isso como prova de que a resposta será de graça; é só o
    # que foi pedido (ver `AIExecutionResult.served_model`/`cost`).
    preferred_model: ModelId | None = None
    required_capabilities: frozenset[ModelCapability] = field(
        default_factory=lambda: frozenset({ModelCapability.CHAT})
    )


@dataclass(frozen=True)
class RouteDecision:
    """Resultado de `ProviderRouter.select()` — uma decisão pura, sem
    nenhuma chamada de rede ainda. `reason` existe para depuração/log
    (nunca para o usuário final tomar decisão de custo com base nela)."""

    provider: ProviderId
    requested_model: ModelId
    reason: str


@dataclass(frozen=True)
class UsageInfo:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class CostInfo:
    """Custo normalizado. `is_free` só é `True`/`False` quando o provider
    de fato informou um valor — `None` significa "provider não relatou
    custo", que o modo free-only trata como NÃO verificado (nunca como
    "provavelmente de graça")."""

    amount: float | None = None
    currency: str = "USD"
    is_free: bool | None = None


@dataclass(frozen=True)
class AIExecutionResult:
    success: bool
    provider: ProviderId
    requested_model: ModelId
    # O que o provider relatou ter de fato servido — pode ser None se a
    # resposta não trouxer essa informação, ou diferente de `requested_model`
    # (ex.: o provider redirecionou pra outra rota). Nunca copiar
    # `requested_model` pra cá "por conveniência".
    served_model: ModelId | None
    output: str = ""
    usage: UsageInfo | None = None
    cost: CostInfo | None = None
    message_id: str | None = None
    duration_ms: float = 0.0
    error: str | None = None
