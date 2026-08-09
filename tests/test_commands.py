import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.commands import JarvisExit
from tests.helpers import build_isolated_core


class CommandsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name))

    def test_help_lists_all_commands(self) -> None:
        response = self.core.handle_input("/help")
        for command in ("/help", "/status", "/memory", "/clear", "/exit"):
            self.assertIn(command, response)

    def test_status_reports_version_state_and_services(self) -> None:
        response = self.core.handle_input("/status")
        self.assertIn("0.1.0", response)
        self.assertIn("idle", response)
        self.assertIn("Memória: disponível", response)
        self.assertIn("Serviço de IA: indisponível", response)

    def test_memory_command_reports_availability_without_dumping_content(self) -> None:
        response = self.core.handle_input("/memory")
        self.assertIn("profile.md: carregado", response)
        self.assertIn("preferences.md: carregado", response)
        self.assertNotIn("Perfil de teste", response)

    def test_exit_raises_jarvis_exit(self) -> None:
        with self.assertRaises(JarvisExit):
            self.core.handle_input("/exit")

    def test_quit_is_alias_for_exit(self) -> None:
        with self.assertRaises(JarvisExit):
            self.core.handle_input("/quit")

    def test_clear_invokes_screen_clear_and_returns_no_text(self) -> None:
        with patch("app.commands.os.system") as mock_system:
            response = self.core.handle_input("/clear")

        mock_system.assert_called_once()
        self.assertEqual(response, "")

    def test_unknown_command_returns_friendly_message(self) -> None:
        response = self.core.handle_input("/nao_existe")
        self.assertIn("Comando desconhecido", response)
        self.assertIn("/help", response)


if __name__ == "__main__":
    unittest.main()
