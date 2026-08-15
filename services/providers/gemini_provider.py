"""`GeminiProvider` — Google Gemini (`generativelanguage.googleapis.com`),
segundo provider da cadeia de fallback (v1.4.0, item 10).

**Protocolo próprio** (por isso não herda `OpenAICompatibleProvider`): a API
do Gemini usa `contents`/`parts` em vez de `messages`, a key vai na
**query string** (não em `Authorization: Bearer`, que é como o Google
documenta esta API — não uma escolha nossa), e a resposta vem em
`candidates[0].content.parts[]` em vez de `choices[0].message`.

Lê `GEMINI_API_KEY`. Desativável via `JARVIS_GEMINI_ENABLED=0`. Modelos
sobrescrevíveis via `JARVIS_GEMINI_MODELS=modelo1,modelo2`.

--------------------------------------------------------------------------
CATÁLOGO — validado por chamada real (item 10: no máximo 2 modelos)
--------------------------------------------------------------------------
`GET /v1beta/models` lista 54 modelos. Descoberta real, batendo direto na
API (não presumida):

    gemini-2.5-pro / gemini-2.5-flash    UNAVAILABLE — HTTP 404: "This model
                                          ... is no longer available to new
                                          users." (apesar de aparecerem na
                                          listagem — só o teste de chamada
                                          real revelou isso)
    gemini-pro-latest                    UNAVAILABLE para esta conta — HTTP
                                          429 "exceeded your current quota,
                                          check your plan and billing" (sem
                                          quota gratuita disponível)
    gemini-3.5-flash                     AVAILABLE — ~1,3s
    gemini-3.5-flash-lite                AVAILABLE — ~0,8s

Ambos os disponíveis também exibem o mesmo padrão de "raciocínio consome o
orçamento de tokens" já documentado para a OpenRouter — a API do Gemini
expõe isso explicitamente via `usageMetadata.thoughtsTokenCount` e partes
marcadas com `"thought": true`. `_parse_gemini_message` separa essas partes
do conteúdo visível (ver função abaixo) em vez de tentar desligar o
raciocínio via `thinkingConfig.thinkingBudget=0`: testado e descartado —
`gemini-flash-latest` ignorou o budget zero (continuou gastando tokens em
pensamento), e `gemini-3.5-flash-lite` respondeu HTTP 400 quando o parâmetro
foi enviado (o modelo não aceita esse campo). A separação estrutural na
resposta é a solução que funciona para todos os modelos, sem depender de um
parâmetro que nem todo modelo aceita.
"""

import json
import logging
import os
import time
import urllib.error

from services.providers.base import AIProvider
from services.providers.env_config import provider_enabled, resolve_model_override
from services.providers.exceptions import InvalidProviderResponseError, ProviderError, ProviderNotConfiguredError
from services.providers.http_support import (
    DEFAULT_USER_AGENT,
    HttpTransport,
    classify_transport_exception,
    raise_for_status,
    urllib_transport,
)
from services.providers.types import (
    AIExecutionResult,
    ModelCapability,
    ModelId,
    ProviderId,
    ProviderMessage,
    ProviderStatus,
    RouteRequest,
    UsageInfo,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_TIMEOUT_S = 60.0
_LABEL = "Gemini"

DEFAULT_MODELS: tuple[ModelId, ...] = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)


def _build_contents(request: RouteRequest) -> list[dict]:
    """`RouteRequest.history`/`prompt` -> formato `contents` do Gemini.
    `role` do Gemini é `"user"`/`"model"` (não `"assistant"`)."""
    contents = []
    for role, content in request.history:
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
    contents.append({"role": "user", "parts": [{"text": request.prompt}]})
    return contents


def _parse_gemini_message(data: dict) -> ProviderMessage:
    """Separa conteúdo visível de raciocínio interno usando o flag
    `"thought": true` que a própria API do Gemini expõe por parte — sem
    isso, um `content: null`-equivalente (candidato sem nenhuma parte de
    texto normal) apareceria como resposta vazia sem explicação.

    Bloqueio por segurança (`finishReason == "SAFETY"` ou
    `promptFeedback.blockReason`) vira `refusal`, nunca conteúdo visível —
    mesma regra estrutural do restante do Provider Router (item 21)."""
    candidates = data.get("candidates") or []
    if not candidates:
        block_reason = (data.get("promptFeedback") or {}).get("blockReason")
        return ProviderMessage(refusal=f"blocked: {block_reason}" if block_reason else None)

    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    visible: list[str] = []
    reasoning: list[str] = []
    for part in parts:
        text = part.get("text")
        if not isinstance(text, str):
            continue
        (reasoning if part.get("thought") else visible).append(text)

    refusal = None
    finish_reason = candidate.get("finishReason")
    if finish_reason == "SAFETY":
        refusal = "blocked: SAFETY"

    return ProviderMessage(
        visible_content="".join(visible), reasoning="".join(reasoning), refusal=refusal
    )


def _parse_gemini_usage(meta: dict) -> UsageInfo:
    return UsageInfo(
        input_tokens=int(meta.get("promptTokenCount", 0) or 0),
        output_tokens=int(meta.get("candidatesTokenCount", 0) or 0),
        total_tokens=int(meta.get("totalTokenCount", 0) or 0),
    )


class GeminiProvider(AIProvider):
    id = ProviderId.GEMINI

    def __init__(
        self,
        *,
        api_key: str | None = None,
        transport: HttpTransport | None = None,
        models: tuple[ModelId, ...] | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        self._enabled = provider_enabled("JARVIS_GEMINI_ENABLED")
        self._transport = transport or urllib_transport
        self._models = models if models is not None else resolve_model_override("JARVIS_GEMINI_MODELS", DEFAULT_MODELS)

    def is_configured(self) -> bool:
        return self._enabled and bool(self._api_key)

    def free_models(self) -> tuple[ModelId, ...]:
        return self._models

    def supported_capabilities(self) -> frozenset[ModelCapability]:
        return frozenset({ModelCapability.CHAT})

    async def health(self) -> ProviderStatus:
        return ProviderStatus.AVAILABLE if self.is_configured() else ProviderStatus.NOT_CONFIGURED

    async def execute(self, request: RouteRequest, *, model: ModelId) -> AIExecutionResult:
        if not self.is_configured():
            raise ProviderNotConfiguredError("GEMINI_API_KEY não está definida (ou provider desativado).")

        payload: dict = {
            "contents": _build_contents(request),
            "generationConfig": {
                "maxOutputTokens": request.max_tokens or 1024,
                "temperature": request.temperature if request.temperature is not None else 0.7,
            },
        }
        if request.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}

        # A key vai na query string — é assim que a API do Gemini exige (não
        # `Authorization: Bearer`). Nunca logada: a URL só existe dentro
        # deste método, e nenhuma exceção deste módulo ecoa a URL completa.
        url = f"{_BASE_URL}/models/{model}:generateContent?key={self._api_key}"
        headers = {"Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT}

        started_at = time.monotonic()
        try:
            response = await self._transport(
                url, headers, json.dumps(payload).encode("utf-8"), request.timeout_s or _DEFAULT_TIMEOUT_S
            )
        except ProviderError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # Estreito de propósito — ver o comentário equivalente em
            # `openrouter_provider.py::execute()` (item 18: bug interno
            # nunca pode ser reclassificado como falha de provider).
            raise classify_transport_exception(exc, provider_label=_LABEL) from exc

        duration_ms = (time.monotonic() - started_at) * 1000

        raise_for_status(response.status, response.body, provider_label=_LABEL)

        try:
            data = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise InvalidProviderResponseError(f"Resposta do {_LABEL} não é JSON válido.") from exc

        message = _parse_gemini_message(data)
        usage = _parse_gemini_usage(data.get("usageMetadata") or {})

        return AIExecutionResult(
            success=True,
            provider=self.id,
            requested_model=model,
            # A API do Gemini não ecoa o model de volta no corpo da
            # resposta — usamos o que foi pedido (item 15: allowlist).
            served_model=model,
            output=message.visible_content,
            reasoning=message.reasoning,
            refusal=message.refusal,
            usage=usage,
            cost=None,
            message_id=None,
            duration_ms=duration_ms,
        )
