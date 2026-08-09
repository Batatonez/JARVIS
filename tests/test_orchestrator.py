import tempfile
import unittest
from pathlib import Path

from app.state import JarvisState
from services.ai_service import AIServiceUnavailableError
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_core


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name))

    async def test_empty_input_returns_empty_response(self) -> None:
        self.assertEqual(await self.core.handle_input(""), "")
        self.assertEqual(await self.core.handle_input("   "), "")

    async def test_plain_message_reports_ai_not_connected(self) -> None:
        response = await self.core.handle_input("olá")
        self.assertTrue(response.startswith("JARVIS:"))
        self.assertIn("não está conectado", response)

    async def test_message_does_not_fake_an_ai_reply(self) -> None:
        response = await self.core.handle_input("faça um resumo disso")
        self.assertNotIn("resumo", response.lower())

    async def test_command_is_routed_to_command_registry(self) -> None:
        response = await self.core.handle_input("/status")
        self.assertIn("JARVIS Core", response)

    async def test_state_returns_to_idle_after_handling_input(self) -> None:
        await self.core.handle_input("olá")
        self.assertEqual(self.core.state.value, "idle")

        await self.core.handle_input("/status")
        self.assertEqual(self.core.state.value, "idle")


class OrchestratorAIMessageTests(unittest.IsolatedAsyncioTestCase):
    """Usa um FakeAIService genérico (não é o ClaudeAgentProvider) para provar
    que o Orchestrator lida com qualquer AIService sem conhecer nada sobre
    Claude ou o Agent SDK."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_successful_ai_reply_emits_request_events_and_stays_idle(self) -> None:
        fake_ai = FakeAIService(available=True, reply="Olá, humano.")
        core = build_isolated_core(Path(self._tmp.name), ai_service=fake_ai)
        events = []
        core.event_bus.subscribe("ai.request.started", lambda **_: events.append("started"))
        core.event_bus.subscribe("ai.request.completed", lambda: events.append("completed"))

        response = await core.handle_input("olá")

        self.assertEqual(response, "JARVIS: Olá, humano.")
        self.assertEqual(events, ["started", "completed"])
        self.assertEqual(core.state.value, "idle")

    async def test_runtime_ai_failure_returns_friendly_message_without_crashing(self) -> None:
        fake_ai = FakeAIService(
            available=True,
            ask_error=AIServiceUnavailableError("O serviço de inteligência retornou um erro."),
        )
        core = build_isolated_core(Path(self._tmp.name), ai_service=fake_ai)
        events = []
        core.event_bus.subscribe("ai.request.failed", lambda **_: events.append("failed"))
        states = []
        core.event_bus.subscribe("state.changed", lambda old, new: states.append(new))

        response = await core.handle_input("olá")

        self.assertTrue(response.startswith("JARVIS:"))
        self.assertIn("erro", response.lower())
        self.assertEqual(events, ["failed"])
        # THINKING -> ERROR -> IDLE
        self.assertEqual(states, [JarvisState.THINKING, JarvisState.ERROR, JarvisState.IDLE])
        self.assertEqual(core.state, JarvisState.IDLE)


if __name__ == "__main__":
    unittest.main()
