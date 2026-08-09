import tempfile
import unittest
from pathlib import Path

from services.memory_service import MemoryService, MemoryUnavailableError


class MemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _write(self, name: str, content: str) -> Path:
        path = self.tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_existing_files(self) -> None:
        profile_path = self._write("profile.md", "# Perfil\nconteúdo de teste")
        preferences_path = self._write("preferences.md", "# Preferências\nconteúdo de teste")

        memory = MemoryService(profile_path, preferences_path)

        self.assertIn("Perfil", memory.get_profile())
        self.assertIn("Preferências", memory.get_preferences())
        self.assertTrue(memory.is_profile_available())
        self.assertTrue(memory.is_preferences_available())

    def test_missing_profile_file(self) -> None:
        missing_path = self.tmp_path / "does_not_exist.md"
        preferences_path = self._write("preferences.md", "conteúdo")

        memory = MemoryService(missing_path, preferences_path)

        self.assertFalse(memory.is_profile_available())
        with self.assertRaises(MemoryUnavailableError):
            memory.get_profile()

    def test_unreadable_file_is_handled_gracefully(self) -> None:
        # Aponta para um diretório em vez de um arquivo: read_text() falha com
        # OSError em qualquer sistema operacional, sem depender de chmod.
        unreadable_path = self.tmp_path / "profile_dir"
        unreadable_path.mkdir()
        preferences_path = self._write("preferences.md", "conteúdo")

        memory = MemoryService(unreadable_path, preferences_path)

        self.assertFalse(memory.is_profile_available())
        with self.assertRaises(MemoryUnavailableError):
            memory.get_profile()


if __name__ == "__main__":
    unittest.main()
