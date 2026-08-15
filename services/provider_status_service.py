"""`ProviderStatusService` — o que a tela "AI PROVIDERS" pode saber sobre os
providers de IA (v1.5.0).

Existe para que a UI NUNCA toque em `os.environ`, num objeto `AIProvider`, ou
numa resposta HTTP crua. Ela recebe uma lista de `ProviderStatusView` já
sanitizada e devolve, no máximo, "testar a conexão deste provider".

--------------------------------------------------------------------------
O que nunca sai daqui
--------------------------------------------------------------------------
API key (nem inteira, nem mascarada, nem os últimos dígitos), header
`Authorization`, URL com key na query string (o Gemini usa esse formato — ver
`services/providers/gemini_provider.py`), corpo de resposta, stack trace.
`configured` é um booleano: a UI sabe "Configurado ✓" ou "Não configurado",
nunca o valor.

`test_connection()` faz **exatamente uma** chamada, ao primeiro modelo da
lista curada do provider, com o menor orçamento de tokens possível. Não é um
benchmark, não percorre a lista inteira de modelos e não tenta de novo em
cima de rate limit — insistir num provider que acabou de dizer "devagar"
seria transformar um botão de diagnóstico em um gerador de bloqueio.

--------------------------------------------------------------------------
O que este módulo NÃO faz (limites explícitos da v1.5.0)
--------------------------------------------------------------------------
- Não é um gerenciador de segredos. As chaves continuam vindo do `.env` via
  `config/env_loader.py`; nada aqui escreve, reescreve ou apaga `.env`.
- Não decide ordem de fallback. `ProviderRouter` e
  `services/providers/env_config.py` continuam sendo a única autoridade — a
  ordem é exibida somente-leitura.
- Não sonda os providers sozinho. Nenhum status é obtido em background nem
  em timer: `test_connection()` só roda quando o usuário clica.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from services.providers.base import AIProvider
from services.providers.exceptions import (
    AuthenticationError,
    CapacityExhaustedError,
    NonRecoverableProviderError,
    ProviderNotConfiguredError,
    RateLimitedError,
    RecoverableProviderError,
)
from services.providers.router import ProviderRouter
from services.providers.types import ProviderId, RouteRequest

logger = logging.getLogger(__name__)

# Rótulos legíveis. Ficam aqui porque a UI não deve inventar nome para um
# `ProviderId` — e um provider novo sem rótulo cairia no `.value`, nunca num
# nome errado.
PROVIDER_LABELS: dict[ProviderId, str] = {
    ProviderId.OPENROUTER: "OpenRouter",
    ProviderId.NVIDIA: "NVIDIA NIM",
    ProviderId.GEMINI: "Google Gemini",
    ProviderId.GROQ: "Groq",
    ProviderId.CEREBRAS: "Cerebras",
    ProviderId.MISTRAL: "Mistral",
}

# Orçamento mínimo viável para provar que a credencial e o endpoint funcionam.
_PROBE_PROMPT = "OK"
_PROBE_MAX_TOKENS = 4
_PROBE_TIMEOUT_S = 20.0


class ProviderHealth(Enum):
    """Vocabulário fechado do que a tela pode dizer. Fechado de propósito:
    sem isso, a tentação seria repassar a mensagem de erro do provider, que é
    exatamente onde corpo de resposta e detalhe de credencial vazam."""

    READY = "ready"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    AUTH_FAILED = "auth_failed"
    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    UNKNOWN = "unknown"


_HEALTH_LABELS: dict[ProviderHealth, str] = {
    ProviderHealth.READY: "Pronto",
    ProviderHealth.RATE_LIMITED: "Limite de uso atingido",
    ProviderHealth.UNAVAILABLE: "Indisponível no momento",
    ProviderHealth.AUTH_FAILED: "Falha de autenticação",
    ProviderHealth.DISABLED: "Desativado",
    ProviderHealth.NOT_CONFIGURED: "Não configurado",
    ProviderHealth.UNKNOWN: "Não testado",
}


@dataclass(frozen=True)
class ProviderStatusView:
    """Tudo que a UI pode saber sobre um provider. Note o que NÃO existe:
    nenhum campo carrega credencial, URL ou detalhe de resposta."""

    provider_id: str
    label: str
    enabled: bool
    configured: bool
    health: ProviderHealth
    models: tuple[str, ...]
    position: int  # posição na ordem de fallback (1 = primeiro tentado)

    @property
    def health_label(self) -> str:
        return _HEALTH_LABELS[self.health]

    @property
    def configuration_label(self) -> str:
        """"Configurado ✓" / "Chave ausente" — nunca a chave, nem mascarada."""
        return "Configurado ✓" if self.configured else "Chave ausente"


def classify_probe_error(exc: Exception) -> ProviderHealth:
    """Exceção do Provider Router -> vocabulário da UI.

    Ordem importa: as mais específicas primeiro, já que `RateLimitedError` e
    `CapacityExhaustedError` são subclasses de `RecoverableProviderError`.

    `CapacityExhaustedError` merece um comentário próprio: é o caso real da
    Cerebras documentado na v1.4.0 (HTTP 402 em todos os modelos do catálogo
    para a conta de teste). A conta ESTÁ configurada, a credencial ESTÁ
    correta — o que falta é quota. "Indisponível no momento" é a leitura
    honesta disso; chamar de falha de autenticação mandaria o usuário
    procurar uma chave nova que não resolveria nada."""
    if isinstance(exc, ProviderNotConfiguredError):
        return ProviderHealth.NOT_CONFIGURED
    if isinstance(exc, AuthenticationError):
        return ProviderHealth.AUTH_FAILED
    if isinstance(exc, RateLimitedError):
        return ProviderHealth.RATE_LIMITED
    if isinstance(exc, CapacityExhaustedError):
        return ProviderHealth.UNAVAILABLE
    if isinstance(exc, NonRecoverableProviderError):
        return ProviderHealth.AUTH_FAILED
    if isinstance(exc, RecoverableProviderError):
        return ProviderHealth.UNAVAILABLE
    return ProviderHealth.UNAVAILABLE


class ProviderStatusService:
    def __init__(self, router: ProviderRouter) -> None:
        self._router = router
        # Último resultado de teste POR provider, só em RAM. Não é cache de
        # decisão: o `ProviderRouter` nunca consulta isto para escolher rota —
        # é só o que a tela mostra até o usuário testar de novo.
        self._last_health: dict[ProviderId, ProviderHealth] = {}

    def list_providers(self) -> list[ProviderStatusView]:
        """Todos os providers da ordem de fallback, na ordem real em que
        seriam tentados. Nenhuma chamada de rede."""
        from services.providers.env_config import DEFAULT_PROVIDER_ORDER

        views: list[ProviderStatusView] = []
        for position, provider_id in enumerate(DEFAULT_PROVIDER_ORDER, start=1):
            provider = self._router.provider(provider_id)
            views.append(self._view(provider_id, provider, position))
        return views

    def _view(
        self, provider_id: ProviderId, provider: AIProvider | None, position: int
    ) -> ProviderStatusView:
        configured = bool(provider is not None and provider.is_configured())
        # `is_configured()` já combina "tem credencial" E "não foi desativado
        # por env" (ver `services/providers/env_config.py::provider_enabled`).
        # Para a UI separar as duas coisas, perguntamos o flag diretamente.
        enabled = bool(provider is not None and self._enabled_flag(provider_id))

        if not enabled:
            health = ProviderHealth.DISABLED
        elif not configured:
            health = ProviderHealth.NOT_CONFIGURED
        else:
            health = self._last_health.get(provider_id, ProviderHealth.UNKNOWN)

        return ProviderStatusView(
            provider_id=provider_id.value,
            label=PROVIDER_LABELS.get(provider_id, provider_id.value),
            enabled=enabled,
            configured=configured,
            health=health,
            models=tuple(provider.free_models()) if provider is not None else (),
            position=position,
        )

    @staticmethod
    def _enabled_flag(provider_id: ProviderId) -> bool:
        """Lê o mesmo `JARVIS_<PROVIDER>_ENABLED` que os providers leem —
        reusando o mecanismo da v1.4.0 em vez de criar um segundo lugar onde
        "desativado" possa significar outra coisa. Desativar por aqui NUNCA
        apaga a API key: o flag e a credencial são independentes."""
        from services.providers.env_config import provider_enabled

        return provider_enabled(f"JARVIS_{provider_id.value.upper()}_ENABLED")

    async def test_connection(self, provider_id: ProviderId) -> ProviderStatusView:
        """UMA chamada mínima ao primeiro modelo curado do provider. Devolve a
        view atualizada — nunca a resposta, nunca o erro cru."""
        provider = self._router.provider(provider_id)
        position = self._position_of(provider_id)

        if provider is None or not self._enabled_flag(provider_id):
            return self._view(provider_id, provider, position)
        if not provider.is_configured():
            self._last_health[provider_id] = ProviderHealth.NOT_CONFIGURED
            return self._view(provider_id, provider, position)

        models = provider.free_models()
        if not models:
            self._last_health[provider_id] = ProviderHealth.UNAVAILABLE
            return self._view(provider_id, provider, position)

        request = RouteRequest(
            prompt=_PROBE_PROMPT,
            max_tokens=_PROBE_MAX_TOKENS,
            timeout_s=_PROBE_TIMEOUT_S,
            free_only=True,
        )
        try:
            await provider.execute(request, model=models[0])
        except Exception as exc:
            # `except Exception` é intencional AQUI (e só aqui): este é um
            # botão de diagnóstico, e um bug interno no meio de um teste de
            # conexão não pode derrubar a tela de configurações. O erro vai
            # para o log, nunca para a UI. Isto não contradiz a regra do
            # `ProviderRouter` (que nunca captura amplamente, para bug interno
            # aparecer): lá o resultado alimenta uma resposta ao usuário; aqui
            # o resultado é um rótulo de status.
            health = classify_probe_error(exc)
            logger.info("Teste de conexão do provider %s: %s", provider_id.value, health.value)
            self._last_health[provider_id] = health
            return self._view(provider_id, provider, position)

        self._last_health[provider_id] = ProviderHealth.READY
        return self._view(provider_id, provider, position)

    @staticmethod
    def _position_of(provider_id: ProviderId) -> int:
        from services.providers.env_config import DEFAULT_PROVIDER_ORDER

        try:
            return DEFAULT_PROVIDER_ORDER.index(provider_id) + 1
        except ValueError:
            return len(DEFAULT_PROVIDER_ORDER) + 1
