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
    ProviderStatus,
    RouteRequest,
    UsageInfo,
)

logger = logging.getLogger(__name__)

# Slug oficial da rota agregada gratuita da OpenRouter — ver
# https://openrouter.ai/docs (modelos com sufixo ":free" ou o alias
# "openrouter/free"/"openrouter/auto:free" também contam como grátis; esta
# constante é só o caso pedido explicitamente pelo projeto nesta etapa).
FREE_MODEL: ModelId = "openrouter/free"

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
        return (FREE_MODEL,)

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

        output = ""
        choices = data.get("choices") or []
        if choices:
            output = choices[0].get("message", {}).get("content", "") or ""

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
            output=output,
            usage=usage,
            cost=cost,
            message_id=data.get("id"),
            duration_ms=duration_ms,
        )


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
