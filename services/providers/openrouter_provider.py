"""`OpenRouterProvider` — primeiro provider real do Provider Router, e o
**primeiro da cadeia de fallback** (v1.4.0, ver `docs/providers.md`). Fala
com a OpenRouter Chat Completions API (`POST /v1/chat/completions`, formato
compatível com OpenAI).

Lê a API key de `OPENROUTER_API_KEY` no ambiente — nunca de um arquivo
versionado, nunca de configuração em código (ver `docs/providers.md`,
seção "Secrets"). Sem a variável, `is_configured()` devolve `False` e
qualquer chamada a `execute()` levanta `ProviderNotConfiguredError` antes
de tocar rede.

**Free-only, honesto**: `served_model`/`cost` em `AIExecutionResult` vêm
só do que a API respondeu (`data["model"]`, `data["usage"]["cost"]` quando
presente) — nunca do que foi pedido. `ProviderRouter` (não este módulo) é
quem decide se isso é aceitável para um pedido `free_only=True`.

**v1.4.0** — o transporte HTTP e o parsing de mensagem passaram a viver em
`services/providers/http_support.py`, compartilhados com os providers novos
(NVIDIA/Groq/Cerebras/Mistral, que falam o mesmo dialeto). `HttpResponse`,
`HttpTransport`, `parse_message` e o comportamento de `OpenRouterProvider`
continuam exportados exatamente como antes — nenhum teste existente precisou
mudar."""

import json
import logging
import os
import time
import urllib.error

from services.providers.base import AIProvider
from services.providers.exceptions import ProviderError
from services.providers.http_support import (
    DEFAULT_USER_AGENT,
    HttpResponse,
    HttpTransport,
    classify_transport_exception,
    parse_openai_chat_message,
    raise_for_status,
    urllib_transport,
)
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

# Reexportados por compatibilidade — `tests/test_provider_router.py` e
# outros módulos importam estes símbolos diretamente daqui desde a v1.0/v1.3.
__all__ = [
    "AGGREGATE_FREE_MODEL",
    "FREE_CHAT_MODELS",
    "FREE_MODEL",
    "HttpResponse",
    "HttpTransport",
    "OpenRouterProvider",
    "parse_message",
]

# Rota agregada gratuita da OpenRouter. **NÃO é a rota padrão** (v1.3.2).
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
# Esta lista envelhece: quando um slug sair do ar, o `ProviderRouter` avança
# para o próximo (v1.4.0 — cadeia de fallback central). Revisar ao atualizar
# a versão.
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
_LABEL = "OpenRouter"


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
        self._transport = transport or urllib_transport

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
        from services.providers.exceptions import ProviderNotConfiguredError

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
            "HTTP-Referer": "https://github.com/Batatonez/JARVIS",
            "X-Title": "JARVIS",
            "User-Agent": DEFAULT_USER_AGENT,
        }

        started_at = time.monotonic()
        try:
            response = await self._transport(
                f"{self._base_url}/v1/chat/completions",
                headers,
                json.dumps(payload).encode("utf-8"),
                request.timeout_s or _DEFAULT_TIMEOUT_S,
            )
        except ProviderError:
            raise  # já classificado por um transporte de teste, repassa intacto
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # Deliberadamente estreito: só exceções de REDE viram
            # `ProviderTimeoutError`/`ProviderConnectionError` (recuperáveis,
            # o loop de fallback pode avançar). Um `TypeError`/`AttributeError`
            # nosso — um bug de verdade — precisa atravessar sem ser
            # reclassificado como "provider indisponível", senão o fallback
            # esconderia exatamente o tipo de erro que precisa aparecer
            # (item 18 do escopo v1.4.0; coberto por
            # `tests/test_provider_router_v14.py::test_36_internal_bug_is_never_masked_by_fallback`).
            raise classify_transport_exception(exc, provider_label=_LABEL) from exc

        duration_ms = (time.monotonic() - started_at) * 1000

        # v1.4.0 — toda falha HTTP vira exceção estruturada (nunca mais um
        # `AIExecutionResult(success=False)`): é isso que permite o
        # `ProviderRouter` decidir "recuperável, tenta o próximo" vs.
        # "não-recuperável, propaga na hora" de forma uniforme com os
        # outros 5 providers (item 6 do escopo: um 401 da OpenRouter nunca
        # pode ser silenciosamente engolido tentando a NVIDIA em seguida).
        raise_for_status(response.status, response.body, provider_label=_LABEL)

        try:
            data = json.loads(response.body)
        except json.JSONDecodeError as exc:
            from services.providers.exceptions import InvalidProviderResponseError

            raise InvalidProviderResponseError("Resposta da OpenRouter não é JSON válido.") from exc

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
            # Cai para o `model` pedido se o campo vier ausente/nulo — a
            # OpenRouter sempre ecoa `model` na prática (confirmado em
            # captura real), então isto nunca muda o comportamento em
            # produção; só torna a verificação free-only por allowlist
            # robusta mesmo que um backend específico omita o campo, igual
            # ao que já vale para NVIDIA/Groq/Cerebras/Mistral (item 15).
            served_model=data.get("model") or model,
            output=message.visible_content,
            reasoning=message.reasoning,
            refusal=message.refusal,
            usage=usage,
            cost=cost,
            message_id=data.get("id"),
            duration_ms=duration_ms,
        )


def parse_message(data: dict):
    """Alias de compatibilidade — a implementação real (compartilhada com
    NVIDIA/Groq/Cerebras/Mistral desde a v1.4.0) mora em
    `services/providers/http_support.py::parse_openai_chat_message`. Ver lá
    para a explicação completa (inclusive o caso `content: null` capturado
    de verdade que motivou a separação `visible_content`/`reasoning`)."""
    return parse_openai_chat_message(data)


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
