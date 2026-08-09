import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.commands import JarvisExit
from tests.helpers import build_isolated_core


class CommandsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name))

    async def test_help_lists_all_commands(self) -> None:
        response = await self.core.handle_input("/help")
        for command in ("/help", "/status", "/memory", "/clear", "/exit"):
            self.assertIn(command, response)

    async def test_status_reports_version_state_and_services(self) -> None:
        response = await self.core.handle_input("/status")
        self.assertIn("0.4.0", response)
        self.assertIn("idle", response)
        self.assertIn("Memória: disponível", response)
        self.assertIn("IA: não configurada", response)
        self.assertIn("Backend de IA: nenhum", response)
        self.assertIn("Sessão: inativa", response)

    async def test_memory_command_reports_availability_without_dumping_content(self) -> None:
        response = await self.core.handle_input("/memory")
        self.assertIn("profile.md: carregado", response)
        self.assertIn("preferences.md: carregado", response)
        self.assertNotIn("Perfil de teste", response)

    async def test_exit_raises_jarvis_exit(self) -> None:
        with self.assertRaises(JarvisExit):
            await self.core.handle_input("/exit")

    async def test_quit_is_alias_for_exit(self) -> None:
        with self.assertRaises(JarvisExit):
            await self.core.handle_input("/quit")

    async def test_clear_invokes_screen_clear_and_returns_no_text(self) -> None:
        with patch("app.commands.os.system") as mock_system:
            response = await self.core.handle_input("/clear")

        mock_system.assert_called_once()
        self.assertEqual(response, "")

    async def test_unknown_command_returns_friendly_message(self) -> None:
        response = await self.core.handle_input("/nao_existe")
        self.assertIn("Comando desconhecido", response)
        self.assertIn("/help", response)


if __name__ == "__main__":
    unittest.main()
