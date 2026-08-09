"""Testes do ClaudeProvider e da factory create_ai_service.

Nenhum teste aqui faz uma chamada real à API da Anthropic — o client é
sempre um fake injetado via o parâmetro `client` do ClaudeProvider.
"""

import unittest
from types import SimpleNamespace

import httpx
import anthropic

from config.settings import Settings
from services.ai_service import UnavailableAIService, create_ai_service
from services.claude_provider import ClaudeProvider, ClaudeProviderError

FAKE_API_KEY = "sk-ant-fake-key-not-real-0000000000000000"


def _fake_client(create=None):
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _text_response(text: str):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class ClaudeProviderTests(unittest.TestCase):
    def test_is_available_is_true(self) -> None:
        provider = ClaudeProvider(
            api_key=FAKE_API_KEY, model="m", timeout=1, max_tokens=10, client=_fake_client()
        )
        self.assertTrue(provider.is_available())

    def test_ask_returns_text_from_response(self) -> None:
        client = _fake_client(create=lambda **kw: _text_response("Olá, humano."))
        provider = ClaudeProvider(
            api_key=FAKE_API_KEY, model="m", timeout=1, max_tokens=10, client=client
        )

        self.assertEqual(provider.ask("oi"), "Olá, humano.")

    def test_ask_sends_model_max_tokens_and_message(self) -> None:
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return _text_response("ok")

        client = _fake_client(create=fake_create)
        provider = ClaudeProvider(
            api_key=FAKE_API_KEY, model="claude-sonnet-5", timeout=1, max_tokens=42, client=client
        )

        provider.ask("qual a capital da frança?")

        self.assertEqual(captured["model"], "claude-sonnet-5")
        self.assertEqual(captured["max_tokens"], 42)
        self.assertEqual(captured["messages"], [{"role": "user", "content": "qual a capital da frança?"}])
        self.assertIn("JARVIS", captured["system"])

    def test_api_status_error_is_translated_and_safe(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(500, request=request)
        error = anthropic.APIStatusError("erro interno", response=response, body=None)

        client = _fake_client(create=lambda **kw: (_ for _ in ()).throw(error))
        provider = ClaudeProvider(
            api_key=FAKE_API_KEY, model="m", timeout=1, max_tokens=10, client=client
        )

        with self.assertRaises(ClaudeProviderError) as ctx:
            provider.ask("oi")
        self.assertNotIn(FAKE_API_KEY, str(ctx.exception))

    def test_api_connection_error_is_translated_and_safe(self) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        error = anthropic.APIConnectionError(request=request)

        client = _fake_client(create=lambda **kw: (_ for _ in ()).throw(error))
        provider = ClaudeProvider(
            api_key=FAKE_API_KEY, model="m", timeout=1, max_tokens=10, client=client
        )

        with self.assertRaises(ClaudeProviderError) as ctx:
            provider.ask("oi")
        self.assertNotIn(FAKE_API_KEY, str(ctx.exception))

    def test_empty_response_is_treated_as_error(self) -> None:
        client = _fake_client(create=lambda **kw: SimpleNamespace(content=[]))
        provider = ClaudeProvider(
            api_key=FAKE_API_KEY, model="m", timeout=1, max_tokens=10, client=client
        )

        with self.assertRaises(ClaudeProviderError):
            provider.ask("oi")


class CreateAIServiceTests(unittest.TestCase):
    def test_no_api_key_returns_unavailable_service(self) -> None:
        settings = Settings(anthropic_api_key=None)

        service = create_ai_service(settings)

        self.assertIsInstance(service, UnavailableAIService)
        self.assertFalse(service.is_available())

    def test_api_key_present_returns_claude_provider(self) -> None:
        settings = Settings(anthropic_api_key=FAKE_API_KEY)

        service = create_ai_service(settings)

        self.assertIsInstance(service, ClaudeProvider)
        self.assertTrue(service.is_available())

    def test_provider_construction_failure_falls_back_safely(self) -> None:
        settings = Settings(anthropic_api_key=FAKE_API_KEY, anthropic_model="m")

        import services.claude_provider as claude_provider_module

        original = claude_provider_module.ClaudeProvider

        def boom(*args, **kwargs):
            raise RuntimeError("falha simulada na construção do client")

        claude_provider_module.ClaudeProvider = boom
        try:
            service = create_ai_service(settings)
        finally:
            claude_provider_module.ClaudeProvider = original

        self.assertIsInstance(service, UnavailableAIService)


if __name__ == "__main__":
    unittest.main()
