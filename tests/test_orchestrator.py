import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
