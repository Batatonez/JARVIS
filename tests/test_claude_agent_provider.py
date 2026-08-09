"""Testes do ClaudeAgentProvider e da factory create_ai_service.

Nenhum teste aqui faz uma chamada real ao Claude Agent SDK — o client é
sempre um `FakeClaudeSDKClient` injetado, ou (nos testes de configuração de
ferramentas) a própria classe `ClaudeSDKClient` é substituída por um
espião que nunca chega a rodar um processo de verdade.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_agent_sdk import (
    AssistantMessage,
    CLINotFoundError,
    PermissionResultDeny,
    ProcessError,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
)

from app.core import JarvisCore
from config.settings import Settings
from services.ai_service import UnavailableAIService, create_ai_service
from services.claude_agent_provider import ClaudeAgentProvider, ClaudeAgentProviderError, _deny_all_tools
from tests.fakes import FakeClaudeSDKClient

FAKE_API_KEY = "sk-ant-fake-key-not-real-0000000000000000"


def _text_message(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="fake-model")


class ClaudeAgentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_connects_the_injected_client(self) -> None:
        client = FakeClaudeSDKClient()
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)

        await provider.start(memory_context="")

        self.assertTrue(client.connected)
        self.assertTrue(provider.session_active)

    async def test_start_is_idempotent(self) -> None:
        client = FakeClaudeSDKClient()
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)

        await provider.start()
        await provider.start()  # não deve reconectar nem trocar de client

        self.assertTrue(provider.session_active)

    async def test_ask_returns_text_from_response(self) -> None:
        client = FakeClaudeSDKClient(responses=[[_text_message("Olá, humano.")]])
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        reply = await provider.ask("oi")

        self.assertEqual(reply, "Olá, humano.")

    async def test_ask_extracts_only_text_blocks_ignoring_thinking(self) -> None:
        message = AssistantMessage(
            content=[ThinkingBlock(thinking="hmm...", signature="sig"), TextBlock(text="Resposta real.")],
            model="fake-model",
        )
        client = FakeClaudeSDKClient(responses=[[message]])
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        reply = await provider.ask("oi")

        self.assertEqual(reply, "Resposta real.")

    async def test_multiple_messages_reuse_the_same_session(self) -> None:
        client = FakeClaudeSDKClient(
            responses=[
                [_text_message("primeira resposta")],
                [_text_message("segunda resposta, com contexto")],
            ]
        )
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        first = await provider.ask("estou trabalhando no BatataMC")
        second = await provider.ask("o que eu acabei de dizer?")

        self.assertEqual(first, "primeira resposta")
        self.assertEqual(second, "segunda resposta, com contexto")
        self.assertEqual(client.queries, ["estou trabalhando no BatataMC", "o que eu acabei de dizer?"])
        # mesma sessão: connect() só foi chamado uma vez, não recriamos o client
        self.assertIs(provider._client, client)

    async def test_ask_before_start_raises_without_crashing(self) -> None:
        provider = ClaudeAgentProvider(model="sonnet", cwd=".")

        with self.assertRaises(ClaudeAgentProviderError):
            await provider.ask("oi")

    async def test_sdk_error_is_translated_and_safe(self) -> None:
        client = FakeClaudeSDKClient(ask_error=ProcessError("boom", exit_code=1, stderr="detalhes internos"))
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        with self.assertRaises(ClaudeAgentProviderError) as ctx:
            await provider.ask("oi")
        # a exceção da abstração nunca deve conter o corpo técnico do erro do SDK
        self.assertNotIn("detalhes internos", str(ctx.exception))

    async def test_connect_error_is_translated_and_safe(self) -> None:
        client = FakeClaudeSDKClient(connect_error=CLINotFoundError(cli_path="/usr/bin/claude"))
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)

        with self.assertRaises(ClaudeAgentProviderError):
            await provider.start()
        self.assertFalse(provider.session_active)

    async def test_ai_error_message_from_assistant_message_is_translated(self) -> None:
        message = AssistantMessage(content=[], model="fake-model", error="rate_limit")
        client = FakeClaudeSDKClient(responses=[[message]])
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        with self.assertRaises(ClaudeAgentProviderError) as ctx:
            await provider.ask("oi")
        self.assertIn("sobrecarregado", str(ctx.exception))

    async def test_result_message_error_without_text_is_translated(self) -> None:
        result = ResultMessage(
            subtype="error", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1, session_id="s1"
        )
        client = FakeClaudeSDKClient(responses=[[result]])
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        with self.assertRaises(ClaudeAgentProviderError):
            await provider.ask("oi")

    async def test_close_disconnects_and_is_idempotent(self) -> None:
        client = FakeClaudeSDKClient()
        provider = ClaudeAgentProvider(model="sonnet", cwd=".", client=client)
        await provider.start()

        await provider.close()
        await provider.close()  # idempotente: não deve levantar nem chamar disconnect de novo

        self.assertTrue(client.disconnected)
        self.assertFalse(provider.session_active)

    async def test_deny_all_tools_denies_with_interrupt(self) -> None:
        result = await _deny_all_tools("Bash", {"command": "rm -rf /"}, object())

        self.assertIsInstance(result, PermissionResultDeny)
        self.assertTrue(result.interrupt)

    async def test_no_tools_or_personal_settings_are_enabled_in_real_options(self) -> None:
        # Aqui NÃO injetamos um client fake: deixamos o provider montar as
        # opções reais e substituímos a própria classe ClaudeSDKClient por um
        # espião que nunca conecta de verdade, só captura o que recebeu.
        captured = {}

        class _SpyClient:
            def __init__(self, options=None):
                captured["options"] = options

            async def connect(self, prompt=None):
                captured["connected"] = True

        with patch("services.claude_agent_provider.ClaudeSDKClient", _SpyClient):
            provider = ClaudeAgentProvider(model="sonnet", cwd=".")
            await provider.start(memory_context="Perfil do usuário:\nteste")

        options = captured["options"]
        self.assertEqual(options.allowed_tools, [])
        self.assertEqual(options.disallowed_tools, [])
        self.assertEqual(options.mcp_servers, {})
        self.assertEqual(options.setting_sources, [])
        self.assertNotEqual(options.permission_mode, "bypassPermissions")
        self.assertIn("Perfil do usuário", options.system_prompt)


class CreateAIServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_no_api_key_returns_unavailable_service(self) -> None:
        settings = Settings(anthropic_api_key=None)

        service = create_ai_service(settings)

        self.assertIsInstance(service, UnavailableAIService)
        self.assertFalse(service.is_available())

    def test_api_key_present_returns_claude_agent_provider(self) -> None:
        settings = Settings(anthropic_api_key=FAKE_API_KEY)

        service = create_ai_service(settings)

        self.assertIsInstance(service, ClaudeAgentProvider)
        self.assertTrue(service.is_available())

    def test_missing_api_key_is_not_treated_as_a_core_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_path = tmp_path / "profile.md"
            preferences_path = tmp_path / "preferences.md"
            profile_path.write_text("teste", encoding="utf-8")
            preferences_path.write_text("teste", encoding="utf-8")
            settings = Settings(
                anthropic_api_key=None,
                profile_path=profile_path,
                preferences_path=preferences_path,
            )

            # Não deve levantar exceção — o Core inicia normalmente sem chave.
            core = JarvisCore(settings=settings)

        self.assertFalse(core.ai_service.is_available())


if __name__ == "__main__":
    unittest.main()
