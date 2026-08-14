"""`ProviderRouterAIService` — implementa a interface `AIService` (a que o
`Orchestrator` já conhece desde a v0.3) por cima do `ProviderRouter`.

    Orchestrator
        ↓  (AIService.ask — interface inalterada)
    ProviderRouterAIService     <-- este módulo
        ↓
    ProviderRouter              (decide provider/modelo/custo)
        ↓
    OpenRouterProvider
        ↓
    modelo

Por que uma classe nova em vez de mexer no `Orchestrator`: a interface
`AIService` (`start`/`ask`/`close` + `is_available`/`session_active`/
`backend_name`) já é exatamente o contrato de "uma sessão de conversa com
uma IA". O `ProviderRouter` fala em requisições isoladas. Este adaptador
guarda o histórico da sessão em RAM e o reenvia a cada chamada — é o que
transforma uma API stateless (OpenRouter) na sessão contínua que o resto do
JARVIS espera. Nada acima disto precisou mudar.

**Ruflo não participa**: nenhuma linha aqui chama `agent_execute` ou
qualquer coisa do Ruflo (ver docs/ruflo-integration.md — o model routing
dele tem bug confirmado e não é fonte de verdade de provider/modelo/custo).
"""

import logging

from app.models import AppErrorCode
from services.ai_service import AIService, AIServiceUnavailableError
from services.providers.exceptions import (
    NoFreeModelAvailableError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    RateLimitedError,
)
from services.providers.router import ProviderRouter
from services.providers.types import ProviderId, RouteRequest
from services.runtime_identity import build_system_prompt as build_runtime_system_prompt

logger = logging.getLogger(__name__)

# Erro de provider -> código estruturado da Application Layer. O HUD nunca
# vê stack trace nem corpo de resposta HTTP (item 66 do escopo v1.0).
_ERROR_CODES: tuple[tuple[type[Exception], AppErrorCode], ...] = (
    (NoFreeModelAvailableError, AppErrorCode.NO_FREE_MODEL_AVAILABLE),
    (RateLimitedError, AppErrorCode.PROVIDER_RATE_LIMITED),
    (ProviderNotConfiguredError, AppErrorCode.PROVIDER_NOT_CONFIGURED),
    (ProviderUnavailableError, AppErrorCode.PROVIDER_UNAVAILABLE),
)


def classify_provider_error(exc: Exception) -> AppErrorCode:
    for exc_type, code in _ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return AppErrorCode.AI_UNAVAILABLE


class ProviderRouterAIService(AIService):
    def __init__(
        self,
        router: ProviderRouter,
        *,
        free_only: bool = True,
        max_tokens: int = 1024,
        timeout_s: float = 60.0,
        max_history_messages: int = 20,
        preferred_provider: ProviderId | None = None,
    ) -> None:
        self._router = router
        self._free_only = free_only
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        # Teto de turnos reenviados. Sem isto, uma conversa longa cresceria
        # o custo/latência de cada chamada indefinidamente (a API é
        # stateless — tudo é reenviado sempre).
        self._max_history_messages = max_history_messages
        self._preferred_provider = preferred_provider

        self._system_prompt: str = ""
        self._history: list[tuple[str, str]] = []
        self._session_active = False
        self._last_result_summary: dict[str, object] | None = None

    # ------------------------------------------------------------------
    # Interface AIService
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Há algum provider configurado? Nunca toca rede — só checa se
        existe credencial (ver `AIProvider.is_configured`)."""
        return bool(self._router.configured_provider_ids())

    @property
    def session_active(self) -> bool:
        return self._session_active

    @property
    def backend_name(self) -> str:
        ids = self._router.configured_provider_ids()
        if not ids:
            return "nenhum"
        name = ids[0].value
        return f"{name} (free)" if self._free_only else name

    async def start(self, *, memory_context: str = "") -> None:
        """Idempotente. Só monta o system prompt e zera o histórico — não há
        conexão a abrir (a API é stateless, cada `ask` é uma requisição HTTP
        independente).

        `memory_context` já chega sanitizado e truncado de
        `JarvisCore.build_memory_context()`; aqui só compomos com a
        identidade de runtime, pela mesma função que o ClaudeAgentProvider
        usa (nenhum system prompt paralelo)."""
        self._system_prompt = build_runtime_system_prompt(memory_context or "")
        self._history = []
        self._session_active = self.is_available()

    async def ask(self, message: str) -> str:
        if not self.is_available():
            raise AIServiceUnavailableError("Nenhum provider de IA está configurado.")

        request = RouteRequest(
            prompt=message,
            system_prompt=self._system_prompt or None,
            history=tuple(self._history[-self._max_history_messages :]),
            max_tokens=self._max_tokens,
            timeout_s=self._timeout_s,
            free_only=self._free_only,
            preferred_provider=self._preferred_provider,
        )

        try:
            result = await self._router.execute(request)
        except ProviderError as exc:
            # Toda falha de provider vira AIServiceUnavailableError com
            # mensagem já segura — é o que o Orchestrator sabe tratar, e o
            # `code` fica disponível para quem quiser diferenciar.
            code = classify_provider_error(exc)
            logger.warning("Falha do provider (%s): %s", code.value, exc)
            raise AIServiceUnavailableError(str(exc)) from exc

        if not result.success:
            raise AIServiceUnavailableError(result.error or "O provider de IA não conseguiu responder.")

        reply = (result.output or "").strip()
        if not reply:
            # Aconteceu de verdade no smoke test da v1.0: um modelo de
            # raciocínio gastou todo o orçamento de saída em raciocínio
            # interno e devolveu texto vazio. Melhor falhar claro do que
            # persistir uma resposta vazia como se fosse válida.
            raise AIServiceUnavailableError(
                "O modelo respondeu sem conteúdo (orçamento de tokens pode ter sido consumido "
                "por raciocínio interno). Tente novamente."
            )

        self._history.append(("user", message))
        self._history.append(("assistant", reply))
        self._last_result_summary = {
            "provider": result.provider.value,
            "requested_model": result.requested_model,
            "served_model": result.served_model,
            "input_tokens": result.usage.input_tokens if result.usage else None,
            "output_tokens": result.usage.output_tokens if result.usage else None,
            "total_tokens": result.usage.total_tokens if result.usage else None,
            "cost": result.cost.amount if result.cost else None,
            "is_free": result.cost.is_free if result.cost else None,
            "duration_ms": round(result.duration_ms, 1),
        }
        return reply

    @property
    def supports_isolated_requests(self) -> bool:
        return True

    async def ask_isolated(self, prompt: str, *, max_tokens: int = 64) -> str:
        """Requisição avulsa (v1.3): sem histórico, sem system prompt de
        identidade, sem gravar nada em `self._history`.

        `free_only` continua sendo o mesmo do serviço — uma tarefa auxiliar
        jamais pode ser a brecha que gasta dinheiro (item 22)."""
        if not self.is_available():
            raise AIServiceUnavailableError("Nenhum provider de IA está configurado.")

        request = RouteRequest(
            prompt=prompt,
            system_prompt=None,
            history=(),
            max_tokens=max_tokens,
            timeout_s=self._timeout_s,
            free_only=self._free_only,
            preferred_provider=self._preferred_provider,
        )
        try:
            result = await self._router.execute(request)
        except ProviderError as exc:
            raise AIServiceUnavailableError(str(exc)) from exc
        if not result.success:
            raise AIServiceUnavailableError(result.error or "Sem resposta do provider.")
        return (result.output or "").strip()

    async def close(self) -> None:
        self._session_active = False
        self._history = []
        self._system_prompt = ""

    # ------------------------------------------------------------------
    # Extra (não faz parte de AIService)
    # ------------------------------------------------------------------

    @property
    def last_result_summary(self) -> dict[str, object] | None:
        """Metadados técnicos da última resposta (provider, modelo servido,
        tokens, custo, duração) — deliberadamente **fora** do texto da
        mensagem (item 57 do escopo). Consumido pelo Bridge para o HUD."""
        return dict(self._last_result_summary) if self._last_result_summary else None
