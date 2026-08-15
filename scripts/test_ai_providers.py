#!/usr/bin/env python
"""Smoke test REAL dos providers de IA (v1.4.0, item 28) — usa as chaves do
`.env` para validar autenticação/endpoint/modelo/latência com uma chamada
mínima por modelo.

    python scripts/test_ai_providers.py

**Opt-in, nunca automático**: não roda no `pytest` (nenhum teste importa
este módulo), não gasta tokens além do estritamente necessário para uma
resposta de 1-2 palavras, e nunca insiste em cima de rate limit — um único
try por modelo, classificado e reportado.

**Nunca imprime segredo.** A API key nunca aparece na saída — nem inteira,
nem mascarada, nem em mensagem de exceção (o `raise_for_status`/
`classify_transport_exception` do próprio Provider Router já garantem isso
nas mensagens de erro; este script não formata nada com a key além de
`Authorization: Bearer` dentro do próprio provider).

Saída (um caractere de status por linha, nunca um dump de resposta crua):

    [OK]   NVIDIA    nvidia/nemotron-3-ultra-550b-a55b        200   843ms
    [FAIL] Groq      llama-3.3-70b-versatile                  429   RATE_LIMITED
    [SKIP] Cerebras  (sem CEREBRAS_API_KEY)
"""

import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.env_loader import load_project_env  # noqa: E402

load_project_env(PROJECT_ROOT, force=True)

from services.providers.base import AIProvider  # noqa: E402
from services.providers.cerebras_provider import CerebrasProvider  # noqa: E402
from services.providers.exceptions import ProviderError, ProviderNotConfiguredError  # noqa: E402
from services.providers.gemini_provider import GeminiProvider  # noqa: E402
from services.providers.groq_provider import GroqProvider  # noqa: E402
from services.providers.mistral_provider import MistralProvider  # noqa: E402
from services.providers.nvidia_provider import NvidiaProvider  # noqa: E402
from services.providers.openrouter_provider import OpenRouterProvider  # noqa: E402
from services.providers.types import RouteRequest  # noqa: E402

_PROMPT = "Reply with OK."
_MAX_TOKENS = 8  # orçamento agressivamente pequeno — item 28: não gastar tokens à toa
_TIMEOUT_S = 90.0  # generoso: um dos modelos NVIDIA validados nesta versão leva ~70s


def _line(status: str, provider_label: str, model: str, detail: str) -> str:
    return f"[{status:<4}] {provider_label:<10} {model:<48} {detail}"


async def _probe(provider: AIProvider, *, provider_label: str) -> list[str]:
    if not provider.is_configured():
        return [_line("SKIP", provider_label, "-", "credencial ausente ou provider desativado")]

    lines = []
    for model in provider.free_models():
        request = RouteRequest(prompt=_PROMPT, max_tokens=_MAX_TOKENS, timeout_s=_TIMEOUT_S, free_only=True)
        started = time.monotonic()
        try:
            result = await provider.execute(request, model=model)
        except ProviderNotConfiguredError:
            lines.append(_line("SKIP", provider_label, model, "não configurado"))
            continue
        except ProviderError as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            category = getattr(exc, "CATEGORY", "UNKNOWN")
            lines.append(_line("FAIL", provider_label, model, f"{category} ({elapsed_ms:.0f}ms)"))
            continue
        elapsed_ms = (time.monotonic() - started) * 1000
        visible = "content" if result.has_visible_content else "sem conteúdo visível (reasoning consumiu o orçamento)"
        lines.append(_line("OK", provider_label, model, f"{elapsed_ms:.0f}ms — {visible}"))
    return lines


async def main() -> int:
    providers: tuple[tuple[AIProvider, str], ...] = (
        (OpenRouterProvider(), "OpenRouter"),
        (NvidiaProvider(), "NVIDIA"),
        (GeminiProvider(), "Gemini"),
        (GroqProvider(), "Groq"),
        (CerebrasProvider(), "Cerebras"),
        (MistralProvider(), "Mistral"),
    )

    print(f"JARVIS — smoke test real de providers (prompt: {_PROMPT!r}, max_tokens={_MAX_TOKENS})\n")
    any_failure = False
    for provider, label in providers:
        for line in await _probe(provider, provider_label=label):
            print(line)
            if line.startswith("[FAIL]"):
                any_failure = True
    print()
    print("Nenhuma API key ou header Authorization foi impresso acima.")
    return 1 if any_failure else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
