"""Transporte HTTP e parsing compartilhados entre todos os providers que
falam uma API REST simples compatível com o dialeto OpenAI Chat Completions
(v1.4.0 — NVIDIA NIM, Groq, Cerebras, Mistral, e o próprio OpenRouter).

Existir como módulo separado evita reimplementar a mesma leitura de
`urllib.request` (bloqueante, rodada em `run_in_executor`) e a mesma
separação `visible_content`/`reasoning`/`refusal` quatro vezes — ver
`services/providers/openai_compatible.py` para a classe base que usa isto.

**Achado real desta versão:** `urllib.request` sem `User-Agent` explícito
é bloqueado pelo WAF da Cloudflare na frente da API da Groq (HTTP 403,
"error code: 1010" — banimento por assinatura de user-agent, não erro de
autenticação). Confirmado batendo na API de verdade durante o
desenvolvimento. Por isso todo request daqui em diante carrega um
`User-Agent` identificável — não é cosmético, é o que faz a Groq responder."""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from services.providers.exceptions import (
    AuthenticationError,
    BadRequestError,
    CapacityExhaustedError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderPermissionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitedError,
)
from services.providers.types import ProviderMessage

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "JARVIS/1.4 (+https://github.com/Batatonez/JARVIS)"

_ERROR_BODY_PREVIEW_CHARS = 300


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str


# Mesma assinatura injetável usada por `OpenRouterProvider` desde a v1.0 —
# testes nunca tocam rede real, injetam um transporte falso aqui.
HttpTransport = Callable[[str, dict[str, str], bytes, float], Awaitable[HttpResponse]]


async def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> HttpResponse:
    """Transporte HTTP real (produção) — `urllib.request` bloqueante, fora
    do event loop via `run_in_executor` (mesmo padrão de
    `services/vosk_model_manager.py` e `services/providers/openrouter_provider.py`
    desde a v1.0). Nunca classifica erro aqui — devolve `HttpResponse` em
    sucesso/4xx-5xx, ou deixa a exceção crua do `urllib` propagar; quem
    classifica é `raise_for_status`/`classify_transport_exception`, chamados
    por cada provider concreto."""
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT, **headers}

    def _do_request() -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=merged_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return HttpResponse(status=response.status, body=response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return HttpResponse(status=exc.code, body=exc.read().decode("utf-8", errors="replace"))

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_request)


def classify_transport_exception(exc: Exception, *, provider_label: str) -> ProviderError:
    """`urllib`/rede -> exceção estruturada. Nunca inclui headers/corpo do
    request (não há credencial em uma exceção de rede, mas por princípio a
    mensagem só descreve o tipo de falha, nunca ecoa o payload)."""
    if isinstance(exc, TimeoutError):
        return ProviderTimeoutError(f"Timeout ao chamar {provider_label}.")
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return ProviderTimeoutError(f"Timeout ao chamar {provider_label}.")
        return ProviderConnectionError(f"Falha de conexão com {provider_label}: {reason}")
    if isinstance(exc, OSError):
        return ProviderConnectionError(f"Falha de conexão com {provider_label}: {exc}")
    return ProviderUnavailableError(f"Falha inesperada ao chamar {provider_label}: {exc}")


def raise_for_status(status: int, body: str, *, provider_label: str) -> None:
    """Levanta a exceção estruturada correspondente ao status HTTP — nunca
    devolve o corpo inteiro na mensagem (só uma prévia curta, útil para
    diagnóstico sem virar um dump de resposta)."""
    if status < 400:
        return
    preview = (body or "")[:_ERROR_BODY_PREVIEW_CHARS]
    if status == 401:
        raise AuthenticationError(f"{provider_label}: credencial rejeitada (401). {preview}")
    if status == 403:
        raise ProviderPermissionError(f"{provider_label}: acesso negado (403). {preview}")
    if status in (400, 422):
        raise BadRequestError(f"{provider_label}: request inválido ({status}). {preview}")
    if status == 402:
        # Confirmado na prática (Cerebras, conta sem billing): "payment
        # required" é a forma de um provider dizer que a quota gratuita
        # desta conta acabou para este modelo — recuperável, tenta o
        # próximo candidato em vez de exigir billing (item 15: nunca
        # ativar cobrança automaticamente).
        raise CapacityExhaustedError(f"{provider_label}: requer pagamento (402). {preview}")
    if status == 404:
        # Confirmado na prática (NVIDIA NIM, `moonshotai/kimi-k2.6`): 404
        # no endpoint de chat completions significa "esse model ID não
        # existe/não está provisionado para esta conta", não uma rota errada.
        raise ModelNotFoundError(f"{provider_label}: modelo não encontrado (404). {preview}")
    if status == 408:
        raise ProviderTimeoutError(f"{provider_label}: timeout reportado pelo servidor (408).")
    if status == 429:
        raise RateLimitedError(f"{provider_label}: rate limit (429). {preview}")
    if status == 503:
        raise CapacityExhaustedError(f"{provider_label}: serviço sobrecarregado (503). {preview}")
    if status >= 500:
        raise ProviderUnavailableError(f"{provider_label}: erro do servidor ({status}). {preview}")
    raise ProviderUnavailableError(f"{provider_label}: resposta inesperada ({status}). {preview}")


def _coerce_text(value: object) -> str:
    """`None`, string, ou lista de partes (`[{"type": "text", "text": "..."}]`,
    formato alternativo aceito por alguns backends compatíveis) -> string.
    Nunca levanta."""
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


# Nomes usados por diferentes backends para a MESMA coisa: a cadeia de
# pensamento interna do modelo. Nenhum destes campos pode virar conteúdo
# visível, em nenhuma circunstância.
#
# A lista é deliberadamente generosa. Um nome aqui que o provider nunca envia
# não custa nada (o `.get()` devolve vazio); um nome que falta é raciocínio
# interno chegando ao chat — foi exatamente o bug relatado na v1.6.0. Os
# nomes vêm dos dialetos observados: `reasoning` (OpenRouter), o
# `reasoning_content` das APIs no estilo DeepSeek/Qwen que NVIDIA NIM e
# Mistral espelham, e `thinking`/`thought`/`analysis` dos modelos que expõem
# o canal de análise separadamente (a família gpt-oss usa "analysis").
_REASONING_FIELDS: tuple[str, ...] = (
    "reasoning",
    "reasoning_content",
    "thinking",
    "thought",
    "thoughts",
    "analysis",
    "internal_reasoning",
)


def extract_reasoning(message: dict) -> str:
    """Junta todo campo de raciocínio presente na mensagem, na ordem
    declarada. Junta (em vez de pegar o primeiro) porque nada garante que um
    backend use um só — e o objetivo aqui não é reconstruir o raciocínio
    fielmente, é garantir que nenhum pedaço dele escape para `content`."""
    parts = [_coerce_text(message.get(field)) for field in _REASONING_FIELDS]
    return "\n".join(part for part in parts if part.strip())


def _extract_tool_calls(message: dict) -> tuple[str, ...]:
    """Só os NOMES das funções chamadas — nunca os argumentos, que podem
    carregar dado do usuário e não têm utilidade fora da execução."""
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return ()
    names = []
    for call in raw:
        if isinstance(call, dict):
            function = call.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                names.append(function["name"])
    return tuple(names)


def parse_openai_chat_message(data: dict) -> ProviderMessage:
    """Separa a mensagem de uma resposta no formato OpenAI Chat Completions
    por natureza — `visible_content` (só isto pode virar mensagem de
    `assistant`), `reasoning` (raciocínio interno, nunca UI) e `refusal`
    (recusa estruturada).

    A causa raiz documentada em `docs/providers.md` (bug "User Safety: safe"
    da v1.3.2) é sobre SELEÇÃO de modelo, não sobre este parser — este
    parser sempre separou os campos corretamente; o problema era perguntar a
    um classificador de conteúdo, não a um modelo de conversa. Reaproveitado
    aqui por TODOS os providers OpenAI-compatible (v1.4.0), incluindo o
    próprio OpenRouter (`openrouter_provider.py::parse_message` é um alias
    fino para esta função, preservado por compatibilidade)."""
    choices = data.get("choices") or []
    if not choices:
        return ProviderMessage()
    choice = choices[0]
    message = choice.get("message") or {}

    return ProviderMessage(
        # `content` e SOMENTE `content`. Nunca `content or reasoning`: uma
        # resposta sem conteúdo visível é uma resposta vazia, e é assim que
        # ela tem que chegar ao router (que então decide sobre fallback).
        visible_content=_coerce_text(message.get("content")),
        reasoning=extract_reasoning(message),
        refusal=message.get("refusal") or None,
        tool_calls=_extract_tool_calls(message),
        finish_reason=choice.get("finish_reason") or None,
    )
