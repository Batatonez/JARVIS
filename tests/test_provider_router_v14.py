"""Cadeia de fallback multi-provider (v1.4.0) — 100% offline.

Nenhum teste aqui toca rede: cada provider recebe um `ScriptedTransport`
(substitui o `urllib_transport` real, mesmo padrão de `FakeHttpTransport`
usado desde a v1.0 em `tests/test_provider_router.py`). As API keys usadas
são todas fixtures óbvias (`"fake-key-..."`), nunca uma credencial real.

Cobre a lista de 40 casos do escopo v1.4.0 (item 32), organizados por seção.
"""

import asyncio
import json
import os
import unittest
import unittest.mock

from services.providers.cerebras_provider import CerebrasProvider
from services.providers.exceptions import (
    AuthenticationError,
    BadRequestError,
    CapacityExhaustedError,
    FallbackExhaustedError,
    ModelNotFoundError,
    NoFreeModelAvailableError,
    ProviderConnectionError,
    ProviderTimeoutError,
)
from services.providers.gemini_provider import GeminiProvider
from services.providers.groq_provider import GroqProvider
from services.providers.http_support import HttpResponse
from services.providers.mistral_provider import MistralProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.openrouter_provider import OpenRouterProvider
from services.providers.registry import ProviderRegistry
from services.providers.router import ProviderRouter
from services.providers.types import ProviderId, RouteRequest


# ----------------------------------------------------------------------
# Infraestrutura de teste — nunca toca rede
# ----------------------------------------------------------------------


class ScriptedTransport:
    """Transporte falso: cada chamada consome o próximo "passo" da lista
    (uma `HttpResponse` para devolver, ou uma exceção para levantar — como
    o `urllib` real faria em caso de timeout/conexão recusada). Repete o
    último passo se a lista se esgotar. Grava toda chamada para inspeção
    (modelo pedido, headers) sem nunca reter nada além do que o teste
    passou explicitamente."""

    def __init__(self, steps: list) -> None:
        self._steps = list(steps)
        self.calls: list[dict] = []

    async def __call__(self, url: str, headers: dict, body: bytes, timeout_s: float) -> HttpResponse:
        payload = json.loads(body.decode())
        self.calls.append({"url": url, "headers": dict(headers), "model": payload.get("model")})
        index = min(len(self.calls) - 1, len(self._steps) - 1)
        step = self._steps[index]
        if isinstance(step, BaseException):
            raise step
        return step

    @property
    def requested_models(self) -> list[str]:
        return [c["model"] for c in self.calls]

    @property
    def call_count(self) -> int:
        return len(self.calls)


def ok(content: str = "resposta", *, model: str | None = None, reasoning: str | None = None) -> HttpResponse:
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    body = {
        "id": "gen-test",
        "model": model,
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    return HttpResponse(status=200, body=json.dumps(body))


def empty(*, reasoning: str = "pensando bastante...") -> HttpResponse:
    return ok(content=None, reasoning=reasoning)  # type: ignore[arg-type]


def http_error(status: int, detail: str = "erro") -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps({"error": {"message": detail}}))


def gemini_ok(content: str = "resposta") -> HttpResponse:
    """A API do Gemini tem um formato de resposta próprio
    (`candidates[0].content.parts[]`, não `choices[0].message`) — usar
    `ok()` (formato OpenAI) para um `GeminiProvider` faria
    `_parse_gemini_message` não achar `candidates` e devolver uma mensagem
    vazia, mascarando qualquer cenário de sucesso como "empty_response"."""
    body = {
        "candidates": [
            {"content": {"parts": [{"text": content}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
    }
    return HttpResponse(status=200, body=json.dumps(body))


def _openrouter(transport, *, key="fake-openrouter-key"):
    return OpenRouterProvider(api_key=key, transport=transport)


def _nvidia(transport, *, key="fake-nvidia-key", models=None):
    return NvidiaProvider(api_key=key, transport=transport, models=models)


def _gemini(transport, *, key="fake-gemini-key", models=None):
    return GeminiProvider(api_key=key, transport=transport, models=models)


def _groq(transport, *, key="fake-groq-key", models=None):
    return GroqProvider(api_key=key, transport=transport, models=models)


def _cerebras(transport, *, key="fake-cerebras-key", models=None):
    return CerebrasProvider(api_key=key, transport=transport, models=models)


def _mistral(transport, *, key="fake-mistral-key", models=None):
    return MistralProvider(api_key=key, transport=transport, models=models)


def _registry(*providers) -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


# ----------------------------------------------------------------------
# 1-7 — OpenRouter é sempre o primeiro; sucesso não chama mais ninguém;
# erro recuperável avança; erro não-recuperável nunca avança.
# ----------------------------------------------------------------------


class OpenRouterFirstTests(unittest.IsolatedAsyncioTestCase):
    async def test_1_openrouter_success_calls_nothing_else(self) -> None:
        or_transport = ScriptedTransport([ok("tudo certo")])
        nvidia_transport = ScriptedTransport([ok("nunca deveria chegar aqui")])
        router = ProviderRouter(_registry(_openrouter(or_transport), _nvidia(nvidia_transport)))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "tudo certo")
        self.assertEqual(nvidia_transport.call_count, 0)
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.fallback_count, 0)

    async def test_2_openrouter_429_falls_back_to_nvidia(self) -> None:
        or_transport = ScriptedTransport([http_error(429)])
        nvidia_transport = ScriptedTransport([ok("nvidia respondeu", model="nvidia/nemotron-3-ultra-550b-a55b")])
        router = ProviderRouter(
            _registry(
                _openrouter(or_transport),
                _nvidia(nvidia_transport, models=("nvidia/nemotron-3-ultra-550b-a55b",)),
            )
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "nvidia respondeu")
        self.assertGreaterEqual(nvidia_transport.call_count, 1)
        self.assertTrue(result.fallback_used)

    async def test_3_openrouter_timeout_falls_back_to_nvidia(self) -> None:
        or_transport = ScriptedTransport([TimeoutError("timed out")])
        nvidia_transport = ScriptedTransport([ok("nvidia respondeu", model="m")])
        router = ProviderRouter(_registry(_openrouter(or_transport), _nvidia(nvidia_transport, models=("m",))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "nvidia respondeu")

    async def test_4_openrouter_500_falls_back_to_nvidia(self) -> None:
        or_transport = ScriptedTransport([http_error(500)])
        nvidia_transport = ScriptedTransport([ok("nvidia respondeu", model="m")])
        router = ProviderRouter(_registry(_openrouter(or_transport), _nvidia(nvidia_transport, models=("m",))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "nvidia respondeu")

    async def test_5_openrouter_503_falls_back_to_nvidia(self) -> None:
        or_transport = ScriptedTransport([http_error(503)])
        nvidia_transport = ScriptedTransport([ok("nvidia respondeu", model="m")])
        router = ProviderRouter(_registry(_openrouter(or_transport), _nvidia(nvidia_transport, models=("m",))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "nvidia respondeu")

    async def test_6_openrouter_401_never_falls_back_silently(self) -> None:
        or_transport = ScriptedTransport([http_error(401)])
        nvidia_transport = ScriptedTransport([ok("nao deveria ser chamado")])
        router = ProviderRouter(_registry(_openrouter(or_transport), _nvidia(nvidia_transport)))

        with self.assertRaises(AuthenticationError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(nvidia_transport.call_count, 0, "401 nunca pode ser mascarado tentando outro provider")

    async def test_7_openrouter_400_never_falls_back_silently(self) -> None:
        or_transport = ScriptedTransport([http_error(400)])
        nvidia_transport = ScriptedTransport([ok("nao deveria ser chamado")])
        router = ProviderRouter(_registry(_openrouter(or_transport), _nvidia(nvidia_transport)))

        with self.assertRaises(BadRequestError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(nvidia_transport.call_count, 0)


# ----------------------------------------------------------------------
# 8-16 — dentro do mesmo provider, entre providers, e exaustão total.
# ----------------------------------------------------------------------


class ChainProgressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_8_nvidia_model_1_recoverable_failure_falls_back_to_model_2(self) -> None:
        or_transport = ScriptedTransport([http_error(429)])  # tira a OpenRouter do caminho
        nvidia_transport = ScriptedTransport([http_error(503), ok("modelo 2 respondeu", model="modelo-2")])
        router = ProviderRouter(
            _registry(
                _openrouter(or_transport),
                _nvidia(nvidia_transport, models=("modelo-1", "modelo-2")),
            )
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "modelo 2 respondeu")
        self.assertEqual(nvidia_transport.requested_models, ["modelo-1", "modelo-2"])

    async def test_9_model_not_found_advances_to_next_model(self) -> None:
        or_transport = ScriptedTransport([http_error(429)])
        nvidia_transport = ScriptedTransport([http_error(404), ok("modelo 2 respondeu", model="modelo-2")])
        router = ProviderRouter(
            _registry(_openrouter(or_transport), _nvidia(nvidia_transport, models=("kimi-inexistente", "modelo-2")))
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "modelo 2 respondeu")

    async def test_10_success_on_model_2_never_calls_the_rest(self) -> None:
        or_transport = ScriptedTransport([http_error(429)])
        nvidia_transport = ScriptedTransport([http_error(503), ok("modelo 2 respondeu", model="modelo-2")])
        gemini_transport = ScriptedTransport([ok("nao deveria ser chamado")])
        router = ProviderRouter(
            _registry(
                _openrouter(or_transport),
                _nvidia(nvidia_transport, models=("modelo-1", "modelo-2", "modelo-3-nunca-tentado")),
                _gemini(gemini_transport),
            )
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "modelo 2 respondeu")
        self.assertEqual(nvidia_transport.call_count, 2, "modelo-3 nunca deveria ser tentado")
        self.assertEqual(gemini_transport.call_count, 0)

    async def test_11_nvidia_exhausted_falls_back_to_gemini(self) -> None:
        or_transport = ScriptedTransport([http_error(429)])
        nvidia_transport = ScriptedTransport([http_error(503)])
        gemini_transport = ScriptedTransport([gemini_ok("gemini respondeu")])
        router = ProviderRouter(
            _registry(
                _openrouter(or_transport),
                _nvidia(nvidia_transport, models=("m1", "m2")),
                _gemini(gemini_transport, models=("gm1",)),
            )
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "gemini respondeu")

    async def test_12_gemini_model_1_failure_falls_back_to_model_2(self) -> None:
        gemini_transport = ScriptedTransport([http_error(503), gemini_ok("gemini modelo 2")])
        router = ProviderRouter(_registry(_gemini(gemini_transport, models=("gm1", "gm2"))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "gemini modelo 2")

    async def test_13_gemini_exhausted_falls_back_to_groq(self) -> None:
        gemini_transport = ScriptedTransport([http_error(503)])
        groq_transport = ScriptedTransport([ok("groq respondeu")])
        router = ProviderRouter(
            _registry(_gemini(gemini_transport, models=("gm1",)), _groq(groq_transport, models=("gq1",)))
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "groq respondeu")

    async def test_14_groq_exhausted_falls_back_to_cerebras(self) -> None:
        groq_transport = ScriptedTransport([http_error(503)])
        cerebras_transport = ScriptedTransport([ok("cerebras respondeu")])
        router = ProviderRouter(
            _registry(_groq(groq_transport, models=("gq1",)), _cerebras(cerebras_transport, models=("c1",)))
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "cerebras respondeu")

    async def test_15_cerebras_exhausted_falls_back_to_mistral(self) -> None:
        # Cenário real desta versão: Cerebras devolve 402 (payment_required)
        # para todos os modelos desta conta — comportamento reproduzido
        # aqui e classificado como recuperável (CapacityExhaustedError).
        cerebras_transport = ScriptedTransport([http_error(402), http_error(402)])
        mistral_transport = ScriptedTransport([ok("mistral respondeu")])
        router = ProviderRouter(
            _registry(
                _cerebras(cerebras_transport, models=("c1", "c2")),
                _mistral(mistral_transport, models=("mi1",)),
            )
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "mistral respondeu")

    async def test_16_all_providers_fail_recoverably_raises_fallback_exhausted(self) -> None:
        router = ProviderRouter(
            _registry(
                _openrouter(ScriptedTransport([http_error(503)])),
                _nvidia(ScriptedTransport([http_error(503)]), models=("m",)),
                _gemini(ScriptedTransport([http_error(503)]), models=("m",)),
                _groq(ScriptedTransport([http_error(503)]), models=("m",)),
                _cerebras(ScriptedTransport([http_error(402)]), models=("m",)),
                _mistral(ScriptedTransport([http_error(503)]), models=("m",)),
            )
        )

        with self.assertRaises(FallbackExhaustedError) as ctx:
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        # Erro estruturado e legível — nunca um "algo deu errado" genérico.
        self.assertGreater(len(ctx.exception.attempts), 0)
        for attempt in ctx.exception.attempts:
            self.assertIn(":", attempt)


# ----------------------------------------------------------------------
# 17-18 — desabilitado / sem key
# ----------------------------------------------------------------------


class SkipConfiguredCleanlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_17_disabled_provider_is_skipped(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"JARVIS_NVIDIA_ENABLED": "0"}, clear=False):
            nvidia = NvidiaProvider(api_key="fake-key", transport=ScriptedTransport([ok("nao deveria chamar")]))
        self.assertFalse(nvidia.is_configured())

        router = ProviderRouter(_registry(_openrouter(ScriptedTransport([ok("openrouter respondeu")])), nvidia))
        result = await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(result.output, "openrouter respondeu")

    async def test_18_missing_api_key_is_skipped_cleanly(self) -> None:
        nvidia = NvidiaProvider(api_key=None, transport=ScriptedTransport([ok("nao deveria chamar")]))
        self.assertFalse(nvidia.is_configured())

        router = ProviderRouter(_registry(_openrouter(ScriptedTransport([ok("openrouter respondeu")])), nvidia))
        result = await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(result.output, "openrouter respondeu")

    async def test_disabled_and_missing_key_never_raises(self) -> None:
        """Nunca crasha, nunca stack trace — ver item 4 do escopo."""
        with unittest.mock.patch.dict(os.environ, {"JARVIS_GROQ_ENABLED": "0"}, clear=False):
            groq = GroqProvider(api_key=None, transport=ScriptedTransport([]))
        self.assertFalse(groq.is_configured())
        registry = _registry(groq)
        router = ProviderRouter(registry)
        with self.assertRaises(NoFreeModelAvailableError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))


# ----------------------------------------------------------------------
# 19-21 — overrides de modelo via env
# ----------------------------------------------------------------------


class ModelOverrideTests(unittest.TestCase):
    def test_19_nvidia_override_preserves_order(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"JARVIS_NVIDIA_MODELS": "zulu,alfa,bravo"}, clear=False):
            nvidia = NvidiaProvider(api_key="fake-key", transport=ScriptedTransport([]))
        self.assertEqual(nvidia.free_models(), ("zulu", "alfa", "bravo"))

    def test_20_every_provider_override_preserves_order(self) -> None:
        cases = (
            ("JARVIS_GEMINI_MODELS", GeminiProvider, "GEMINI_API_KEY"),
            ("JARVIS_GROQ_MODELS", GroqProvider, "GROQ_API_KEY"),
            ("JARVIS_CEREBRAS_MODELS", CerebrasProvider, "CEREBRAS_API_KEY"),
            ("JARVIS_MISTRAL_MODELS", MistralProvider, "MISTRAL_API_KEY"),
        )
        for env_var, cls, _key_var in cases:
            with self.subTest(env_var=env_var):
                with unittest.mock.patch.dict(os.environ, {env_var: "z-modelo,a-modelo,m-modelo"}, clear=False):
                    provider = cls(api_key="fake-key", transport=ScriptedTransport([]))
                self.assertEqual(provider.free_models(), ("z-modelo", "a-modelo", "m-modelo"))

    def test_21_empty_entries_are_ignored(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"JARVIS_NVIDIA_MODELS": "modelo-a,, ,modelo-b,"}, clear=False):
            nvidia = NvidiaProvider(api_key="fake-key", transport=ScriptedTransport([]))
        self.assertEqual(nvidia.free_models(), ("modelo-a", "modelo-b"))

    def test_override_absent_falls_back_to_defaults(self) -> None:
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_NVIDIA_MODELS", None)
            nvidia = NvidiaProvider(api_key="fake-key", transport=ScriptedTransport([]))
        self.assertEqual(nvidia.free_models(), NvidiaProvider.default_models)

    def test_whitespace_only_override_falls_back_to_defaults(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"JARVIS_NVIDIA_MODELS": "   "}, clear=False):
            nvidia = NvidiaProvider(api_key="fake-key", transport=ScriptedTransport([]))
        self.assertEqual(nvidia.free_models(), NvidiaProvider.default_models)


# ----------------------------------------------------------------------
# 22-23 — nenhum secret em log/exceção
# ----------------------------------------------------------------------


class SecretLeakageTests(unittest.IsolatedAsyncioTestCase):
    SECRET = "sk-real-looking-secret-should-never-appear-anywhere-12345"

    async def test_22_secret_never_appears_in_logs(self) -> None:
        with self.assertLogs(level="DEBUG") as captured:
            transport = ScriptedTransport([http_error(503), http_error(503)])
            router = ProviderRouter(
                _registry(
                    _openrouter(transport, key=self.SECRET),
                    _nvidia(ScriptedTransport([http_error(503)]), key=self.SECRET, models=("m",)),
                )
            )
            with self.assertRaises(FallbackExhaustedError):
                await router.execute(RouteRequest(prompt="oi", free_only=True))
        blob = "\n".join(captured.output)
        self.assertNotIn(self.SECRET, blob)

    async def test_23_secret_never_appears_in_sanitized_exception_message(self) -> None:
        for status in (401, 403, 400, 429, 500, 503):
            with self.subTest(status=status):
                transport = ScriptedTransport([http_error(status, detail="algo falhou")])
                router = ProviderRouter(_registry(_openrouter(transport, key=self.SECRET)))
                with self.assertRaises(Exception) as ctx:
                    await router.execute(RouteRequest(prompt="oi", free_only=True, preferred_provider=ProviderId.OPENROUTER))
                self.assertNotIn(self.SECRET, str(ctx.exception))

    async def test_secret_never_appears_in_gemini_query_string_errors(self) -> None:
        """A key do Gemini vai na query string da URL (item 10) — o caso
        mais fácil de vazar sem querer."""
        transport = ScriptedTransport([ProviderConnectionError("timeout")])
        router = ProviderRouter(_registry(_gemini(transport, key=self.SECRET, models=("m",))))
        with self.assertRaises(Exception) as ctx:
            await router.execute(
                RouteRequest(prompt="oi", free_only=True, preferred_provider=ProviderId.GEMINI, preferred_model="m")
            )
        self.assertNotIn(self.SECRET, str(ctx.exception))


# ----------------------------------------------------------------------
# 24-25 — segurança de streaming (arquitetura atômica: sem streaming real,
# a garantia vale por construção — ver docstring de router.py)
# ----------------------------------------------------------------------


class StreamingSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_24_failure_before_any_visible_content_allows_fallback(self) -> None:
        transport_a = ScriptedTransport([http_error(503)])  # falha ANTES de qualquer conteúdo
        transport_b = ScriptedTransport([ok("conteudo do segundo provider")])
        router = ProviderRouter(_registry(_openrouter(transport_a), _nvidia(transport_b, models=("m",))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(result.output, "conteudo do segundo provider")

    async def test_25_success_never_triggers_another_call_even_if_a_third_candidate_exists(self) -> None:
        """Depois de `has_visible_content=True`, o loop retorna
        IMEDIATAMENTE — nenhum outro provider é tocado, então duas
        respostas nunca podem se misturar na mesma mensagem."""
        transport_a = ScriptedTransport([ok("primeira resposta visivel")])
        transport_b = ScriptedTransport([ok("jamais deveria ser chamado")])
        router = ProviderRouter(_registry(_openrouter(transport_a), _nvidia(transport_b, models=("m",))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "primeira resposta visivel")
        self.assertEqual(transport_b.call_count, 0)


# ----------------------------------------------------------------------
# 26-27 — reasoning/metadata nunca vira conteúdo visível
# ----------------------------------------------------------------------


class MetadataSeparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_26_reasoning_never_becomes_content(self) -> None:
        transport = ScriptedTransport(
            [ok("resposta de verdade", reasoning="um raciocinio interno bem longo e detalhado")]
        )
        router = ProviderRouter(_registry(_openrouter(transport)))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "resposta de verdade")
        self.assertNotIn("raciocinio interno", result.output)
        self.assertIn("raciocinio interno", result.reasoning)

    async def test_27_provider_metadata_never_becomes_content(self) -> None:
        transport = ScriptedTransport([ok("resposta limpa", model="algum/modelo:free")])
        router = ProviderRouter(_registry(_openrouter(transport)))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        for leak in ("prompt_tokens", "completion_tokens", "gen-test", "usage"):
            self.assertNotIn(leak, result.output)


# ----------------------------------------------------------------------
# 28-29 — free_only nunca é contornado
# ----------------------------------------------------------------------


class FreeOnlyEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_28_free_only_respected_across_the_whole_chain(self) -> None:
        # NVIDIA "confirma" um modelo que NÃO está na allowlist configurada
        # — deve ser rejeitado e a cadeia segue adiante.
        nvidia_transport = ScriptedTransport([ok("resposta", model="nvidia/modelo-pago-nao-listado")])
        gemini_transport = ScriptedTransport([gemini_ok("gemini gratuito confirmado")])
        router = ProviderRouter(
            _registry(
                _nvidia(nvidia_transport, models=("nvidia/modelo-gratuito-listado",)),
                _gemini(gemini_transport, models=("gm1",)),
            )
        )
        # Força o pedido de um modelo que o transporte vai "trocar" na
        # resposta por um não-listado — simula um provider mentindo sobre
        # o served_model.
        result = await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(result.output, "gemini gratuito confirmado")

    async def test_29_explicit_paid_model_is_never_chosen_silently(self) -> None:
        provider = _openrouter(ScriptedTransport([ok("nao deveria ser alcancado")]))
        registry = _registry(provider)
        router = ProviderRouter(registry)
        # Nenhum modelo pago aparece em `free_models()` — pedir free_only
        # sem um candidato configurado nunca inventa um modelo pago.
        candidates = router._candidates(RouteRequest(prompt="oi", free_only=True))
        for candidate in candidates:
            self.assertIn(candidate.model, provider.free_models())


# ----------------------------------------------------------------------
# 30-31 — no máximo uma tentativa por candidato, sem loop infinito
# ----------------------------------------------------------------------


class BoundedAttemptsTests(unittest.IsolatedAsyncioTestCase):
    async def test_30_each_provider_model_tried_at_most_once(self) -> None:
        nvidia_transport = ScriptedTransport([http_error(503), http_error(503), ok("por fim")])
        router = ProviderRouter(_registry(_nvidia(nvidia_transport, models=("m1", "m2", "m3"))))

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(result.output, "por fim")
        self.assertEqual(len(nvidia_transport.requested_models), len(set(nvidia_transport.requested_models)))

    async def test_31_no_infinite_loop_with_a_large_but_finite_candidate_list(self) -> None:
        many_models = tuple(f"modelo-{i}" for i in range(50))
        transport = ScriptedTransport([http_error(503)] * 50)
        router = ProviderRouter(_registry(_nvidia(transport, models=many_models)))

        with self.assertRaises(FallbackExhaustedError):
            await asyncio.wait_for(router.execute(RouteRequest(prompt="oi", free_only=True)), timeout=5.0)
        self.assertEqual(transport.call_count, 50)


# ----------------------------------------------------------------------
# 32-33 — cancelamento
# ----------------------------------------------------------------------


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_32_cancellation_stops_the_whole_chain(self) -> None:
        async def _slow_transport(url, headers, body, timeout_s):
            await asyncio.sleep(5.0)
            return ok("nunca deveria retornar")

        nvidia_transport = ScriptedTransport([ok("jamais chamado")])
        router = ProviderRouter(_registry(_openrouter(_slow_transport), _nvidia(nvidia_transport, models=("m",))))

        task = asyncio.ensure_future(router.execute(RouteRequest(prompt="oi", free_only=True)))
        await asyncio.sleep(0.02)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_33_cancellation_never_triggers_another_fallback_attempt(self) -> None:
        call_log: list[str] = []

        async def _slow_first(url, headers, body, timeout_s):
            call_log.append("openrouter")
            await asyncio.sleep(5.0)
            return ok("nunca")

        async def _never_called(url, headers, body, timeout_s):
            call_log.append("nvidia")
            return ok("nunca deveria acontecer")

        router = ProviderRouter(_registry(_openrouter(_slow_first), _nvidia(_never_called, models=("m",))))
        task = asyncio.ensure_future(router.execute(RouteRequest(prompt="oi", free_only=True)))
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(call_log, ["openrouter"], "cancelamento nunca pode iniciar outro fallback")


# ----------------------------------------------------------------------
# 34-36 — classificação de timeout/conexão, bug interno nunca mascarado
# ----------------------------------------------------------------------


class ErrorClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_34_timeout_is_classified_correctly(self) -> None:
        transport = ScriptedTransport([TimeoutError("timed out")])
        router = ProviderRouter(_registry(_openrouter(transport)))
        with self.assertRaises(FallbackExhaustedError) as ctx:
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertIn("TIMEOUT", ctx.exception.attempts[0])

    async def test_35_connection_refused_is_classified_correctly(self) -> None:
        import urllib.error

        transport = ScriptedTransport([urllib.error.URLError(ConnectionRefusedError("recusado"))])
        router = ProviderRouter(_registry(_openrouter(transport)))
        with self.assertRaises(FallbackExhaustedError) as ctx:
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertIn("CONNECTION_ERROR", ctx.exception.attempts[0])

    async def test_36_internal_bug_is_never_masked_by_fallback(self) -> None:
        """Um TypeError nosso nunca pode ser "resolvido" tentando o próximo
        provider — isso esconderia exatamente o tipo de bug que precisa
        aparecer (item 18)."""

        async def _buggy_transport(url, headers, body, timeout_s):
            raise TypeError("bug interno do JARVIS, nao do provider")

        nvidia_transport = ScriptedTransport([ok("jamais deveria ser chamado")])
        router = ProviderRouter(_registry(_openrouter(_buggy_transport), _nvidia(nvidia_transport, models=("m",))))

        with self.assertRaises(TypeError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        self.assertEqual(nvidia_transport.call_count, 0)


# ----------------------------------------------------------------------
# 37-40 — coerência final, compatibilidade sem keys, telemetria, AIService
# ----------------------------------------------------------------------


class FinalCoherenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_37_disabled_plus_all_others_failing_gives_coherent_error(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"JARVIS_NVIDIA_ENABLED": "0"}, clear=False):
            nvidia = NvidiaProvider(api_key="fake-key", transport=ScriptedTransport([ok("nunca")]))
        or_transport = ScriptedTransport([http_error(503)])
        router = ProviderRouter(_registry(_openrouter(or_transport), nvidia))

        with self.assertRaises(FallbackExhaustedError) as ctx:
            await router.execute(RouteRequest(prompt="oi", free_only=True))
        # Só a OpenRouter aparece nas tentativas (uma por modelo gratuito
        # dela) — NVIDIA nunca entrou na cadeia porque estava desativada,
        # não porque "falhou": não sobra nenhum vestígio dela na lista.
        self.assertEqual(len(ctx.exception.attempts), len(OpenRouterProvider(api_key="x").free_models()))
        self.assertTrue(all(attempt.startswith("openrouter/") for attempt in ctx.exception.attempts))

    async def test_38_absence_of_all_external_keys_preserves_compatible_behavior(self) -> None:
        """Sem NENHUMA key configurada, o comportamento é idêntico ao de
        antes desta versão: `NoFreeModelAvailableError`/`ProviderNotConfiguredError`,
        nunca um crash."""
        registry = _registry(
            OpenRouterProvider(api_key=None, transport=ScriptedTransport([])),
            NvidiaProvider(api_key=None, transport=ScriptedTransport([])),
        )
        router = ProviderRouter(registry)
        with self.assertRaises(NoFreeModelAvailableError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))

    async def test_39_fallback_telemetry_is_reported(self) -> None:
        transport_a = ScriptedTransport([http_error(503)])
        transport_b = ScriptedTransport([http_error(503)])
        transport_c = ScriptedTransport([gemini_ok("finalmente")])
        openrouter_model_count = len(OpenRouterProvider(api_key="x").free_models())
        router = ProviderRouter(
            _registry(
                _openrouter(transport_a),
                _nvidia(transport_b, models=("m",)),
                _gemini(transport_c, models=("m",)),
            )
        )

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertTrue(result.fallback_used)
        # Todos os modelos gratuitos da OpenRouter falharam (1 tentativa
        # cada) + 1 modelo da NVIDIA falhou, antes do Gemini finalmente
        # responder — a telemetria soma exatamente essas tentativas.
        self.assertEqual(result.fallback_count, openrouter_model_count + 1)

    async def test_40_ai_service_flow_remains_compatible(self) -> None:
        """Fim a fim pelo `ProviderRouterAIService` — a interface pública
        (`start`/`ask`/`close`) continua idêntica; só o que acontece por
        baixo mudou."""
        from services.provider_ai_service import ProviderRouterAIService

        transport_a = ScriptedTransport([http_error(429)])
        transport_b = ScriptedTransport([ok("resposta final ao usuario")])
        router = ProviderRouter(_registry(_openrouter(transport_a), _nvidia(transport_b, models=("m",))))
        service = ProviderRouterAIService(router, free_only=True)

        await service.start()
        reply = await service.ask("Opa, tudo bem?")

        self.assertEqual(reply, "resposta final ao usuario")
        summary = service.last_result_summary
        self.assertTrue(summary["fallback_used"])
        # Não uma contagem exata: quantos modelos da OpenRouter foram
        # tentados antes de cair para a NVIDIA é um detalhe interno da lista
        # de modelos gratuitos dela, não o que este teste verifica — o que
        # importa é que ALGUM fallback aconteceu e a resposta final é a
        # certa.
        self.assertGreaterEqual(summary["fallback_count"], 1)


if __name__ == "__main__":
    unittest.main()
