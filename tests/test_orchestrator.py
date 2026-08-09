import tempfile
import unittest
from pathlib import Path

from services.ai_service import AIService, AIServiceUnavailableError
from tests.helpers import build_isolated_core


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name))

    def test_empty_input_returns_empty_response(self) -> None:
        self.assertEqual(self.core.handle_input(""), "")
        self.assertEqual(self.core.handle_input("   "), "")

    def test_plain_message_reports_ai_not_connected(self) -> None:
        response = self.core.handle_input("olá")
        self.assertTrue(response.startswith("JARVIS:"))
        self.assertIn("não está conectado", response)

    def test_message_does_not_fake_an_ai_reply(self) -> None:
        response = self.core.handle_input("faça um resumo disso")
        self.assertNotIn("resumo", response.lower())

    def test_command_is_routed_to_command_registry(self) -> None:
        response = self.core.handle_input("/status")
        self.assertIn("JARVIS Core", response)

    def test_state_returns_to_idle_after_handling_input(self) -> None:
        self.core.handle_input("olá")
        self.assertEqual(self.core.state.value, "idle")

        self.core.handle_input("/status")
        self.assertEqual(self.core.state.value, "idle")


class _FailingAIService(AIService):
    """Fake genérico (não é o ClaudeProvider) usado só para provar que o
    Orchestrator lida com qualquer AIService que falhe em runtime, sem
    conhecer nada sobre Claude ou a SDK da Anthropic."""

    def is_available(self) -> bool:
        return True

    def ask(self, message: str) -> str:
        raise AIServiceUnavailableError("O serviço de inteligência retornou um erro.")


class OrchestratorAIFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name), ai_service=_FailingAIService())

    def test_runtime_ai_failure_returns_friendly_message_without_crashing(self) -> None:
        response = self.core.handle_input("olá")

        self.assertTrue(response.startswith("JARVIS:"))
        self.assertIn("erro", response.lower())

    def test_state_returns_to_idle_after_ai_failure(self) -> None:
        self.core.handle_input("olá")

        self.assertEqual(self.core.state.value, "idle")


if __name__ == "__main__":
    unittest.main()
