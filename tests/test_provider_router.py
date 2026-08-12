"""Testes do Provider Router — 100% offline. `FakeHttpTransport` nunca abre
socket algum; usado no lugar do transporte real (`urllib`) de
`OpenRouterProvider`. Nenhum teste aqui pode gastar uma requisição real
(item 19 do pedido) — ver também `tests/test_ruflo_coordinator.py`.
"""

import asyncio
import json
import unittest

from services.providers.exceptions import (
    NoFreeModelAvailableError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    RateLimitedError,
)
from services.providers.openrouter_provider import FREE_MODEL, HttpResponse, OpenRouterProvider
from services.providers.registry import ProviderRegistry
from services.providers.router import ProviderRouter
from services.providers.secrets import mask_secret
from services.providers.types import ProviderId, ProviderStatus, RouteRequest


class FakeHttpTransport:
    """Substitui `HttpTransport` — devolve respostas canned em vez de
    tocar rede. Grava toda chamada em `self.calls` pra testes inspecionarem
    URL/headers/body (ex.: garantir que a API key nunca vaza num log)."""

    def __init__(self, *, response: HttpResponse | None = None, raise_exc: Exception | None = None, delay: float = 0.0):
        self.response = response
        self.raise_exc = raise_exc
        self.delay = delay
        self.calls: list[dict] = []

    async def __call__(self, url: str, headers: dict, body: bytes, timeout_s: float) -> HttpResponse:
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout_s": timeout_s})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.response is not None
        return self.response


def _openrouter_success_response(*, model: str = FREE_MODEL, cost: float | None = 0.0) -> HttpResponse:
    usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    if cost is not None:
        usage["cost"] = cost
    body = {
        "id": "gen-test-123",
        "model": model,
        "choices": [{"message": {"content": "JARVIS FREE ROUTER OK"}, "finish_reason": "stop"}],
        "usage": usage,
    }
    return HttpResponse(status=200, body=json.dumps(body))


class OpenRouterProviderTests(unittest.IsolatedAsyncioTestCase):
    # 1. key ausente ----------------------------------------------------
    def test_missing_key_is_not_configured(self) -> None:
        provider = OpenRouterProvider(api_key=None, transport=FakeHttpTransport())
        self.assertFalse(provider.is_configured())

    async def test_missing_key_raises_before_any_transport_call(self) -> None:
        transport = FakeHttpTransport()
        provider = OpenRouterProvider(api_key=None, transport=transport)
        with self.assertRaises(ProviderNotConfiguredError):
            await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)
        self.assertEqual(transport.calls, [])  # nunca tocou "rede"

    # 2. provider configurado --------------------------------------------
    async def test_configured_provider_reports_available_health(self) -> None:
        provider = OpenRouterProvider(api_key="sk-or-fake-key-000", transport=FakeHttpTransport())
        self.assertTrue(provider.is_configured())
        self.assertEqual(await provider.health(), ProviderStatus.AVAILABLE)

    # 3. seleção openrouter/free ------------------------------------------
    async def test_router_selects_openrouter_free_for_free_only_request(self) -> None:
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=FakeHttpTransport()))
        router = ProviderRouter(registry)

        decision = await router.select(RouteRequest(prompt="oi", free_only=True))

        self.assertEqual(decision.provider, ProviderId.OPENROUTER)
        self.assertEqual(decision.requested_model, FREE_MODEL)

    # 4. resposta normalizada -----------------------------------------------
    async def test_execute_normalizes_response_fields(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response())
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        result = await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

        self.assertTrue(result.success)
        self.assertEqual(result.provider, ProviderId.OPENROUTER)
        self.assertEqual(result.requested_model, FREE_MODEL)
        self.assertEqual(result.served_model, FREE_MODEL)
        self.assertEqual(result.output, "JARVIS FREE ROUTER OK")
        self.assertEqual(result.message_id, "gen-test-123")

    # 5. usage normalizado ---------------------------------------------------
    async def test_execute_normalizes_usage(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response())
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        result = await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.input_tokens, 10)
        self.assertEqual(result.usage.output_tokens, 4)
        self.assertEqual(result.usage.total_tokens, 14)
        self.assertIsNotNone(result.cost)
        self.assertEqual(result.cost.amount, 0.0)
        self.assertTrue(result.cost.is_free)

    async def test_execute_leaves_cost_unknown_when_provider_omits_it(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response(cost=None))
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        result = await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

        self.assertIsNone(result.cost)  # nunca presumido como grátis por omissão

    # 6. erro de rede -----------------------------------------------------
    async def test_network_error_raises_provider_unavailable(self) -> None:
        import urllib.error

        transport = FakeHttpTransport(raise_exc=urllib.error.URLError("sem conexão"))
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        with self.assertRaises(ProviderUnavailableError):
            await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

    # 7. rate limit -----------------------------------------------------------
    async def test_rate_limit_response_raises_rate_limited_error(self) -> None:
        transport = FakeHttpTransport(response=HttpResponse(status=429, body="{}"))
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        with self.assertRaises(RateLimitedError):
            await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

    async def test_server_error_response_raises_provider_unavailable(self) -> None:
        transport = FakeHttpTransport(response=HttpResponse(status=503, body="{}"))
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        with self.assertRaises(ProviderUnavailableError):
            await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

    # 8. provider unavailable (não configurado) -----------------------------
    async def test_router_raises_when_no_provider_configured(self) -> None:
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key=None, transport=FakeHttpTransport()))
        router = ProviderRouter(registry)

        with self.assertRaises(ProviderNotConfiguredError):
            await router.select(RouteRequest(prompt="oi"))

    # 9. free-only recusando fallback pago -----------------------------------
    async def test_free_only_raises_when_served_model_is_not_confirmed_free(self) -> None:
        # Exatamente o bug documentado: pedimos free, o "provider" (fake)
        # devolve um served_model pago sem cost=0 — o router tem que
        # detectar isso e recusar, nunca devolver como sucesso silencioso.
        transport = FakeHttpTransport(
            response=_openrouter_success_response(model="anthropic/claude-sonnet-5", cost=None)
        )
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=transport))
        router = ProviderRouter(registry)

        with self.assertRaises(NoFreeModelAvailableError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))

    async def test_free_only_raises_when_provider_reports_nonzero_cost(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response(model=FREE_MODEL, cost=0.002))
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=transport))
        router = ProviderRouter(registry)

        with self.assertRaises(NoFreeModelAvailableError):
            await router.execute(RouteRequest(prompt="oi", free_only=True))

    async def test_free_only_accepts_confirmed_free_response(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response(model=FREE_MODEL, cost=0.0))
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=transport))
        router = ProviderRouter(registry)

        result = await router.execute(RouteRequest(prompt="oi", free_only=True))

        self.assertTrue(result.success)
        self.assertEqual(result.served_model, FREE_MODEL)

    async def test_free_only_never_requests_a_paid_model_when_no_free_model_exists(self) -> None:
        class _NoFreeProvider(OpenRouterProvider):
            def free_models(self):
                return ()

        registry = ProviderRegistry()
        registry.register(_NoFreeProvider(api_key="sk-or-fake", transport=FakeHttpTransport()))
        router = ProviderRouter(registry)

        with self.assertRaises(NoFreeModelAvailableError):
            await router.select(RouteRequest(prompt="oi", free_only=True))

    # 10. provider registry ---------------------------------------------------
    def test_registry_lists_openrouter_and_planned_providers(self) -> None:
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=FakeHttpTransport()))

        descriptors = {d.id: d for d in registry.descriptors()}

        self.assertEqual(descriptors[ProviderId.OPENROUTER].status, ProviderStatus.AVAILABLE)
        self.assertIn(FREE_MODEL, descriptors[ProviderId.OPENROUTER].free_models)
        for planned in (ProviderId.GROQ, ProviderId.GEMINI, ProviderId.MISTRAL, ProviderId.NVIDIA, ProviderId.ANTHROPIC):
            self.assertEqual(descriptors[planned].status, ProviderStatus.NOT_IMPLEMENTED)

    def test_registry_reports_not_configured_without_key(self) -> None:
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key=None, transport=FakeHttpTransport()))

        descriptors = {d.id: d for d in registry.descriptors()}
        self.assertEqual(descriptors[ProviderId.OPENROUTER].status, ProviderStatus.NOT_CONFIGURED)

    # 11. nenhum secret em logs ------------------------------------------------
    def test_mask_secret_never_returns_full_key(self) -> None:
        key = "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789"
        masked = mask_secret(key)
        self.assertNotEqual(masked, key)
        self.assertNotIn(key, masked)
        self.assertTrue(masked.endswith(key[-4:]))

    def test_mask_secret_handles_missing_key(self) -> None:
        self.assertEqual(mask_secret(None), "not_configured")
        self.assertEqual(mask_secret(""), "not_configured")

    async def test_api_key_is_only_sent_as_bearer_header_never_in_body(self) -> None:
        key = "sk-or-v1-should-never-leak-anywhere-else"
        transport = FakeHttpTransport(response=_openrouter_success_response())
        provider = OpenRouterProvider(api_key=key, transport=transport)

        await provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL)

        call = transport.calls[0]
        self.assertEqual(call["headers"]["Authorization"], f"Bearer {key}")
        self.assertNotIn(key, json.dumps(call["body"]))  # a key nunca aparece no corpo da requisição

    # 12. cancelamento --------------------------------------------------------
    async def test_execute_can_be_cancelled_mid_flight(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response(), delay=5.0)
        provider = OpenRouterProvider(api_key="sk-or-fake", transport=transport)

        task = asyncio.ensure_future(provider.execute(RouteRequest(prompt="oi"), model=FREE_MODEL))
        await asyncio.sleep(0.01)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task


class ProviderRouterHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_status_for_every_known_provider(self) -> None:
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=FakeHttpTransport()))
        router = ProviderRouter(registry)

        health = await router.health()

        self.assertEqual(health[ProviderId.OPENROUTER], ProviderStatus.AVAILABLE)
        self.assertEqual(health[ProviderId.GROQ], ProviderStatus.NOT_IMPLEMENTED)


if __name__ == "__main__":
    unittest.main()
