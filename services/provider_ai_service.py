"""`ProviderRouterAIService` — implementa a interface `AIService` (a que o
`Orchestrator` já conhece desde a v0.3) por cima do `ProviderRouter`.

    Orchestrator
        ↓  (AIService.ask — interface inalterada)
    ProviderRouterAIService     <-- este módulo
        ↓
    ProviderRouter              (decide provider/modelo/ordem/fallback/custo)
        ↓
    OpenRouter | NVIDIA | Gemini | Groq | Cerebras | Mistral
        ↓
    modelo

Por que uma classe nova em vez de mexer no `Orchestrator`: a interface
`AIService` (`start`/`ask`/`close` + `is_available`/`session_active`/
`backend_name`) já é exatamente o contrato de "uma sessão de conversa com
uma IA". O `ProviderRouter` fala em requisições isoladas. Este adaptador
guarda o histórico da sessão em RAM e o reenvia a cada chamada — é o que
transforma uma API stateless em sessão contínua. Nada acima disto precisou
mudar.

**v1.4.0**: toda a lógica de fallback entre providers/modelos (que até a
v1.3.2 vivia aqui, como um retry estreito de "resposta vazia") migrou para
`ProviderRouter.execute()` — este módulo não decide mais nada sobre ordem,
retry ou candidatos alternativos (item 2 do escopo: fallback é
responsabilidade centralizada do router, nunca do `AIService`). O que
permanece aqui é só o que é genuinamente responsabilidade de sessão:
histórico da conversa, system prompt, e tradução de exceção para
`AIServiceUnavailableError`.

**Ruflo não participa**: nenhuma linha aqui chama `agent_execute` ou
qualquer coisa do Ruflo (ver docs/ruflo-integration.md — o model routing
dele tem bug confirmado e não é fonte de verdade de provider/modelo/custo).
"""

import logging

from app.models import AppErrorCode
from services.ai_service import AIService, AIServiceUnavailableError
from services.providers.exceptions import (
    EmptyProviderResponseError,
    FallbackExhaustedError,
    NonRecoverableProviderError,
    NoFreeModelAvailableError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRefusedError,
    ProviderUnavailableError,
    RateLimitedError,
)
from services.providers.router import ProviderRouter
from services.providers.types import ProviderId, RouteRequest
from services.runtime_identity import build_system_prompt as build_runtime_system_prompt

logger = logging.getLogger(__name__)

# Erro de provider -> código estruturado da Application Layer. O HUD nunca
# vê stack trace nem corpo de resposta HTTP (item 66 do escopo v1.0). Ordem
# importa: o primeiro `isinstance` que bater vence, então as exceções mais
# específicas vêm antes das genéricas (RateLimitedError antes de
# ProviderUnavailableError, que ela mesma estende).
_ERROR_CODES: tuple[tuple[type[Exception], AppErrorCode], ...] = (
    # v1.6.0 — ANTES de qualquer entrada genérica: recusa tem código próprio
    # e nunca deve ser lida como erro de configuração ou indisponibilidade.
    (ProviderRefusedError, AppErrorCode.PROVIDER_REFUSED),
    (FallbackExhaustedError, AppErrorCode.FALLBACK_EXHAUSTED),
    (NoFreeModelAvailableError, AppErrorCode.NO_FREE_MODEL_AVAILABLE),
    (EmptyProviderResponseError, AppErrorCode.EMPTY_PROVIDER_RESPONSE),
    # v1.4.0 — qualquer erro não-recuperável (credencial inválida, request
    # malformado, resposta fora do schema) cai neste código único: nunca é
    # mascarado por fallback, e o HUD precisa saber que é um problema de
    # configuração/bug, não uma instabilidade transitória do provider.
    (NonRecoverableProviderError, AppErrorCode.PROVIDER_CONFIGURATION_ERROR),
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
        self._preferences = None
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

    async def start(self, *, memory_context: str = "", preferences=None) -> None:
        """Idempotente. Só monta o system prompt e zera o histórico — não há
        conexão a abrir (a API é stateless, cada `ask` é uma requisição HTTP
        independente).

        `memory_context` já chega sanitizado e truncado de
        `JarvisCore.build_memory_context()`; aqui só compomos com a
        identidade de runtime, pela mesma função que o ClaudeAgentProvider
        usa (nenhum system prompt paralelo)."""
        # v1.6.0 — as diretrizes de idioma/região/moeda entram no system
        # prompt, que é reenviado em TODA chamada desta sessão. É isso que
        # faz o fallback preservar o idioma: o `RouteRequest.system_prompt` é
        # o mesmo objeto para todos os candidatos da cadeia, então trocar de
        # provider no meio de uma request não troca o idioma da resposta
        # (item 49/82).
        self._preferences = preferences
        self._system_prompt = build_runtime_system_prompt(memory_context or "", preferences)
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
            # Toda falha da cadeia inteira (ou de um candidato único
            # explícito) vira AIServiceUnavailableError com mensagem já
            # segura — é o que o Orchestrator sabe tratar, e o `code`
            # (via `classify_provider_error`) fica disponível para quem
            # quiser diferenciar.
            code = classify_provider_error(exc)
            logger.warning("Falha do provider (%s): %s", code.value, exc)
            raise AIServiceUnavailableError(str(exc)) from exc

        reply = result.output.strip()

        if result.refusal is not None:
            # v1.6.0 — recusa NÃO entra no histórico reenviado ao provider.
            #
            # Bug observado: uma mensagem recusada fazia o JARVIS continuar
            # recusando as seguintes ("pq nao?", "adasdasdasd"). A causa não
            # era uma flag de bloqueio — não existe nenhuma no projeto — e sim
            # o histórico: a recusa era gravada como turno de `assistant` e
            # reenviada a cada chamada, então o modelo lia a própria recusa e
            # se ancorava nela.
            #
            # O PAR inteiro (pergunta + recusa) sai. Remover só a recusa
            # deixaria uma pergunta do usuário sem resposta no histórico, o
            # que além de desbalancear a alternância de papéis que alguns
            # providers exigem, convidaria o modelo a "completar" a lacuna.
            #
            # Isto NÃO desativa safety: a recusa foi produzida, foi mostrada
            # ao usuário, e a próxima mensagem será avaliada normalmente pelo
            # provider — que continua livre para recusar de novo se for o
            # caso. O que deixa de existir é a recusa se auto-perpetuando.
            logger.info("Recusa estruturada não entra no histórico da sessão (escopo: esta request).")
            self._last_result_summary = self._summarize(result)
            return reply

        self._history.append(("user", message))
        self._history.append(("assistant", reply))
        self._last_result_summary = self._summarize(result)
        return reply

    @staticmethod
    def _summarize(result) -> dict[str, object]:
        """Metadata técnica da última resposta. Note que `reasoning` entra só
        como TAMANHO, nunca como texto: o raciocínio interno não pode vazar
        nem para o painel de diagnóstico do HUD.

        `fallback_used`/`fallback_count` (v1.4.0, item 26) vêm do
        `ProviderRouter` — este adaptador só repassa."""
        return {
            "provider": result.provider.value,
            "requested_model": result.requested_model,
            "served_model": result.served_model,
            "input_tokens": result.usage.input_tokens if result.usage else None,
            "output_tokens": result.usage.output_tokens if result.usage else None,
            "total_tokens": result.usage.total_tokens if result.usage else None,
            "cost": result.cost.amount if result.cost else None,
            "is_free": result.cost.is_free if result.cost else None,
            "duration_ms": round(result.duration_ms, 1),
            "reasoning_chars": len(result.reasoning or ""),
            "refused": result.refusal is not None,
            "fallback_used": result.fallback_used,
            "fallback_count": result.fallback_count,
        }

    @property
    def supports_isolated_requests(self) -> bool:
        return True

    async def ask_isolated(self, prompt: str, *, max_tokens: int = 64) -> str:
        """Requisição avulsa (v1.3): sem histórico, sem system prompt de
        identidade, sem gravar nada em `self._history`.

        `free_only` continua sendo o mesmo do serviço — uma tarefa auxiliar
        jamais pode ser a brecha que gasta dinheiro (item 22 da v1.3 / item 3
        da v1.4.0). Também passa pela cadeia completa de fallback do router,
        de graça — nenhuma lógica duplicada aqui."""
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
        tokens, custo, duração, uso de fallback) — deliberadamente **fora**
        do texto da mensagem (item 57 do escopo v1.0). Consumido pelo Bridge
        para o HUD."""
        return dict(self._last_result_summary) if self._last_result_summary else None
