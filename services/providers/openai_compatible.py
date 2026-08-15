"""`OpenAICompatibleProvider` — base para providers que falam o dialeto
OpenAI Chat Completions (v1.4.0): NVIDIA NIM, Groq, Cerebras e Mistral direto
usam literalmente o mesmo formato de request/resposta que a OpenRouter já
falava desde a v1.0.

Item 5/27 do escopo v1.4.0: reutilizar a abstração existente em vez de somar
"5 SDKs gigantes". Cada subclasse concreta (`nvidia_provider.py`,
`groq_provider.py`, `cerebras_provider.py`, `mistral_provider.py`) só declara
identidade — nome da env var da API key, URL base, lista de modelos padrão —
e herda toda a mecânica HTTP/parsing/classificação de erro daqui.

**Sem cost/usage.cost confiável** (item 15): nenhum destes providers reporta
custo por chamada como a OpenRouter faz. A garantia free-only aqui é
**allowlist pura**: `free_models()` só devolve a lista curada/validada por
smoke test real, `execute()` sempre ecoa `served_model` igual ao modelo
pedido, e `ProviderRouter._verify_free_or_raise` confirma gratuidade batendo
`served_model` contra essa lista (nunca via `cost`, que fica sempre `None`
aqui). Ver `docs/providers.md` para a limitação documentada."""

import json
import logging
import os
import time
import urllib.error
from abc import ABC

from services.providers.base import AIProvider
from services.providers.env_config import provider_enabled, resolve_model_override
from services.providers.exceptions import InvalidProviderResponseError, ProviderError, ProviderNotConfiguredError
from services.providers.http_support import (
    DEFAULT_USER_AGENT,
    HttpTransport,
    classify_transport_exception,
    parse_openai_chat_message,
    raise_for_status,
    urllib_transport,
)
from services.providers.types import (
    AIExecutionResult,
    ModelCapability,
    ModelId,
    ProviderId,
    ProviderStatus,
    RouteRequest,
    UsageInfo,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 60.0


class OpenAICompatibleProvider(AIProvider, ABC):
    """Subclasses declaram estes atributos de classe; nada mais precisa ser
    reimplementado para um provider OpenAI-compatible simples de chat."""

    id: ProviderId
    label: str  # nome legível para mensagem de erro, ex.: "NVIDIA NIM"
    env_key_name: str  # ex.: "NVIDIA_API_KEY"
    env_enabled_name: str  # ex.: "JARVIS_NVIDIA_ENABLED"
    env_models_name: str  # ex.: "JARVIS_NVIDIA_MODELS"
    base_url: str  # ex.: "https://integrate.api.nvidia.com/v1" (sem barra final)
    default_models: tuple[ModelId, ...]

    def __init__(
        self,
        *,
        api_key: str | None = None,
        transport: HttpTransport | None = None,
        models: tuple[ModelId, ...] | None = None,
    ) -> None:
        # Mesmo padrão de `OpenRouterProvider` desde a v1.0: `api_key=None`
        # lê do ambiente na hora; testes injetam uma chave explícita.
        self._api_key = api_key if api_key is not None else os.environ.get(self.env_key_name)
        self._enabled = provider_enabled(self.env_enabled_name)
        self._transport = transport or urllib_transport
        self._models = (
            models if models is not None else resolve_model_override(self.env_models_name, self.default_models)
        )

    def is_configured(self) -> bool:
        """Item 4: desativado via env OU sem key — nos dois casos, o
        candidato simplesmente não entra na cadeia de fallback. O router não
        precisa (nem deve) distinguir "desligado de propósito" de "esqueceu
        de configurar" — os dois produzem exatamente o mesmo skip limpo."""
        return self._enabled and bool(self._api_key)

    def free_models(self) -> tuple[ModelId, ...]:
        return self._models

    def supported_capabilities(self) -> frozenset[ModelCapability]:
        return frozenset({ModelCapability.CHAT})

    async def health(self) -> ProviderStatus:
        return ProviderStatus.AVAILABLE if self.is_configured() else ProviderStatus.NOT_CONFIGURED

    async def execute(self, request: RouteRequest, *, model: ModelId) -> AIExecutionResult:
        if not self.is_configured():
            raise ProviderNotConfiguredError(f"{self.env_key_name} não está definida (ou provider desativado).")

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for role, content in request.history:
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }

        started_at = time.monotonic()
        try:
            response = await self._transport(
                f"{self.base_url}/chat/completions",
                headers,
                json.dumps(payload).encode("utf-8"),
                request.timeout_s or _DEFAULT_TIMEOUT_S,
            )
        except ProviderError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # Estreito de propósito — ver o comentário equivalente em
            # `openrouter_provider.py::execute()`: um bug nosso (TypeError,
            # AttributeError) nunca pode ser reclassificado como falha de
            # provider, senão o fallback o esconderia (item 18).
            raise classify_transport_exception(exc, provider_label=self.label) from exc

        duration_ms = (time.monotonic() - started_at) * 1000

        raise_for_status(response.status, response.body, provider_label=self.label)

        try:
            data = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise InvalidProviderResponseError(f"Resposta da {self.label} não é JSON válido.") from exc

        message = parse_openai_chat_message(data)
        usage_raw = data.get("usage") or {}
        usage = UsageInfo(
            input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
        )

        return AIExecutionResult(
            success=True,
            provider=self.id,
            requested_model=model,
            # Providers OpenAI-compatible ecoam `model` de volta no corpo.
            # Cai para o `model` pedido se o campo vier ausente — é o que
            # mantém a verificação free-only por allowlist funcionando mesmo
            # que um backend específico omita o campo (item 15).
            served_model=data.get("model") or model,
            output=message.visible_content,
            reasoning=message.reasoning,
            refusal=message.refusal,
            usage=usage,
            cost=None,  # nenhum destes providers reporta custo por chamada
            message_id=data.get("id"),
            duration_ms=duration_ms,
        )
