"""`OpenRouterProvider` — primeiro (e único, nesta etapa) provider real do
Provider Router. Fala com a OpenRouter Chat Completions API
(`POST /v1/chat/completions`, formato compatível com OpenAI) via `urllib`
da stdlib (mesma escolha de `services/vosk_model_manager.py`: evita somar
`requests`/`httpx`/`aiohttp` como dependência nova só para isto).

Lê a API key de `OPENROUTER_API_KEY` no ambiente — nunca de um arquivo
versionado, nunca de configuração em código (ver `docs/providers.md`,
seção "Secrets"). Sem a variável, `is_configured()` devolve `False` e
qualquer chamada a `execute()` levanta `ProviderNotConfiguredError` antes
de tocar rede.

**Free-only, honesto**: `served_model`/`cost` em `AIExecutionResult` vêm
só do que a API respondeu (`data["model"]`, `data["usage"]["cost"]` quando
presente) — nunca do que foi pedido. `ProviderRouter` (não este módulo) é
quem decide se isso é aceitável para um pedido `free_only=True`.
"""

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from services.providers.base import AIProvider
from services.providers.exceptions import ProviderNotConfiguredError, ProviderUnavailableError, RateLimitedError
from services.providers.types import (
    AIExecutionResult,
    CostInfo,
    ModelCapability,
    ModelId,
    ProviderId,
    ProviderMessage,
    ProviderStatus,
    RouteRequest,
    UsageInfo,
)

logger = logging.getLogger(__name__)

# Rota agregada gratuita da OpenRouter. **NÃO é mais a rota padrão** (v1.3.2).
#
# CAUSA RAIZ DO BUG "User Safety: safe": `openrouter/free` é um agregador CEGO
# — ele sorteia qualquer modelo do pool gratuito a cada chamada. Nesse pool
# está `nvidia/nemotron-3.5-content-safety:free`, que **não é um modelo de
# chat**: é um CLASSIFICADOR de conteúdo. Perguntando "Opa! E aí, tudo bem?"
# a ele, a resposta (capturada de verdade, em `tests/fixtures_openrouter.py`)
# é literalmente:
#
#     {"role": "assistant", "content": "User Safety: safe", ...}
#
# O parser estava certo; quem estava errado era a SELEÇÃO. Nenhum filtro de
# texto resolveria isso sem quebrar uma resposta legítima que por acaso
# contenha as mesmas palavras.
#
# Continua disponível via `RouteRequest.preferred_model` para quem quiser
# conscientemente a rota agregada.
AGGREGATE_FREE_MODEL: ModelId = "openrouter/free"

# Modelos gratuitos de CHAT, em ordem de preferência. Curado à mão porque a
# metadata da API não distingue um classificador de um modelo de conversa
# (ambos declaram `text -> text`), então não há como filtrar automaticamente.
#
# Critérios de exclusão aplicados ao pool gratuito atual:
#   - `nvidia/nemotron-3.5-content-safety` -> classificador, não conversa
#   - `poolside/laguna-*`, `cohere/north-mini-code` -> modelos de CÓDIGO
#   - `*-omni-*-reasoning` -> gastou o orçamento inteiro em raciocínio e
#     devolveu `content: null` na captura real
#
# Esta lista envelhece: quando um slug sair do ar, o próximo da tupla assume
# (ver o retry de `ProviderRouterAIService`). Revisar ao atualizar a versão.
FREE_CHAT_MODELS: tuple[ModelId, ...] = (
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
)

# Compatibilidade: `FREE_MODEL` era o slug único da v1.0-v1.3.
FREE_MODEL: ModelId = FREE_CHAT_MODELS[0]

_DEFAULT_BASE_URL = "https://openrouter.ai/api"
_DEFAULT_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str


# Assinatura do transporte HTTP — injetável para testes (nunca bate em rede
# de verdade em `tests/`, ver `tests/fakes.py::FakeHttpTransport`).
HttpTransport = Callable[[str, dict[str, str], bytes, float], Awaitable[HttpResponse]]


async def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> HttpResponse:
    """Transporte HTTP real (produção) — `urllib.request` bloqueante, rodado
    fora do event loop via `run_in_executor` (mesmo padrão de
    `services/vosk_model_manager.py`)."""

    def _do_request() -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return HttpResponse(status=response.status, body=response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=exc.code, body=exc.read().decode("utf-8", errors="replace"))

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_request)


class OpenRouterProvider(AIProvider):
    id = ProviderId.OPENROUTER

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        transport: HttpTransport | None = None,
    ) -> None:
        # `api_key=None` (o caso normal) lê do ambiente na hora — nunca
        # congelado em disco/config. Testes injetam uma chave falsa/None
        # explicitamente via este mesmo parâmetro.
        self._api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _urllib_transport

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def free_models(self) -> tuple[ModelId, ...]:
        """Modelos gratuitos de CHAT, na ordem de preferência.

        `AGGREGATE_FREE_MODEL` entra no fim: continua sendo uma rota gratuita
        válida (o `free_only` do router precisa reconhecê-la como tal quando
        alguém a pede explicitamente), mas nunca é a escolha automática —
        ver o comentário de `AGGREGATE_FREE_MODEL`."""
        return FREE_CHAT_MODELS + (AGGREGATE_FREE_MODEL,)

    def supported_capabilities(self) -> frozenset[ModelCapability]:
        return frozenset({ModelCapability.CHAT, ModelCapability.STREAMING})

    async def health(self) -> ProviderStatus:
        return ProviderStatus.AVAILABLE if self.is_configured() else ProviderStatus.NOT_CONFIGURED

    async def execute(self, request: RouteRequest, *, model: ModelId) -> AIExecutionResult:
        if not self.is_configured():
            raise ProviderNotConfiguredError("OPENROUTER_API_KEY não está definida no ambiente.")

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        # Histórico primeiro, prompt atual por último — a ordem é o que dá
        # sentido de conversa ao modelo (ver RouteRequest.history).
        for role, content in request.history:
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
            "temperature": request.temperature if request.temperature is not None else 0.7,
            # Pede à OpenRouter para incluir custo normalizado na resposta,
            # quando o backend suportar — é o que permite `CostInfo.amount`
            # ser um valor real em vez de sempre `None`.
            "usage": {"include": True},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/anthropics/claude-code",
            "X-Title": "JARVIS",
        }

        started_at = time.monotonic()
        try:
            response = await self._transport(
                f"{self._base_url}/v1/chat/completions",
                headers,
                json.dumps(payload).encode("utf-8"),
                request.timeout_s or _DEFAULT_TIMEOUT_S,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderUnavailableError(f"Falha de rede ao chamar a OpenRouter: {exc}") from exc

        duration_ms = (time.monotonic() - started_at) * 1000

        if response.status == 429:
            raise RateLimitedError("OpenRouter respondeu 429 (rate limit).")
        if response.status >= 500:
            raise ProviderUnavailableError(f"OpenRouter respondeu {response.status} (erro do servidor).")
        if response.status >= 400:
            return AIExecutionResult(
                success=False,
                provider=self.id,
                requested_model=model,
                served_model=None,
                error=f"OpenRouter API error {response.status}: {response.body[:400]}",
                duration_ms=duration_ms,
            )

        try:
            data = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise ProviderUnavailableError("Resposta da OpenRouter não é JSON válido.") from exc

        message = parse_message(data)

        usage_raw = data.get("usage") or {}
        usage = UsageInfo(
            input_tokens=int(usage_raw.get("prompt_tokens", 0)),
            output_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
        )
        cost = _normalize_cost(usage_raw)

        return AIExecutionResult(
            success=True,
            provider=self.id,
            requested_model=model,
            served_model=data.get("model"),
            output=message.visible_content,
            reasoning=message.reasoning,
            refusal=message.refusal,
            usage=usage,
            cost=cost,
            message_id=data.get("id"),
            duration_ms=duration_ms,
        )


def parse_message(data: dict) -> ProviderMessage:
    """Separa a mensagem do provider por natureza (v1.3.2).

    A resposta compatível com OpenAI carrega, no MESMO objeto `message`,
    coisas de naturezas diferentes — e só `content` é destinado ao usuário.
    Captura real de `nvidia/nemotron-3-nano-omni-...:free`:

        {"role": "assistant", "content": null, "reasoning": "Okay, the user
         is greeting me in Portuguese...", "refusal": null}

    Ou seja: `content` pode ser `null` mesmo com `success=True` e usage
    contabilizado. Antes isso virava string vazia sem distinção; agora o
    chamador consegue perguntar `has_visible_content` e tratar como resposta
    ausente em vez de mostrar nada (ou, pior, cair em algum fallback que
    aproveitasse o raciocínio).

    `content` também pode vir como LISTA de partes (`[{"type": "text",
    "text": "..."}]`) em alguns backends — as partes de texto são
    concatenadas na ordem, e qualquer parte que não seja texto é ignorada.
    """
    choices = data.get("choices") or []
    if not choices:
        return ProviderMessage()
    message = choices[0].get("message") or {}

    return ProviderMessage(
        visible_content=_coerce_text(message.get("content")),
        reasoning=_coerce_text(message.get("reasoning")),
        refusal=message.get("refusal") or None,
    )


def _coerce_text(value) -> str:
    """`None`, string, ou lista de partes -> string. Nunca levanta."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def _normalize_cost(usage_raw: dict) -> CostInfo | None:
    """A OpenRouter só devolve `usage.cost` quando o pedido inclui
    `usage: {include: true}` (já feito acima) e o backend específico
    suporta isso — quando ausente, `CostInfo.amount`/`is_free` ficam `None`
    (desconhecido), nunca presumidos como zero."""
    if "cost" not in usage_raw:
        return None
    amount = usage_raw.get("cost")
    if amount is None:
        return None
    amount = float(amount)
    return CostInfo(amount=amount, currency="USD", is_free=amount == 0.0)
