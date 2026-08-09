"""Acesso somente-leitura à memória em Markdown (`memory/profile.md` e `memory/preferences.md`).

Esta é a única porta de entrada que o resto do sistema deve usar para ler a
memória — nenhum outro módulo deve abrir esses arquivos diretamente. Escrita
de memória não é implementada nesta versão; isso terá regras próprias no
futuro (ver `CLAUDE.md`, seção "Memória").
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryUnavailableError(Exception):
    """Levantada quando um arquivo de memória não existe ou não pode ser lido."""


class MemoryService:
    def __init__(self, profile_path: Path, preferences_path: Path) -> None:
        self._profile_path = Path(profile_path)
        self._preferences_path = Path(preferences_path)

    def get_profile(self) -> str:
        return self._read(self._profile_path, "profile")

    def get_preferences(self) -> str:
        return self._read(self._preferences_path, "preferences")

    def is_profile_available(self) -> bool:
        return self._is_available(self._profile_path, "profile")

    def is_preferences_available(self) -> bool:
        return self._is_available(self._preferences_path, "preferences")

    def _is_available(self, path: Path, label: str) -> bool:
        try:
            self._read(path, label)
            return True
        except MemoryUnavailableError:
            return False

    def _read(self, path: Path, label: str) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            logger.warning("Arquivo de memória '%s' não encontrado em: %s", label, path)
            raise MemoryUnavailableError(
                f"Arquivo de memória '{label}' não encontrado."
            ) from exc
        except OSError as exc:
            logger.error("Erro ao ler arquivo de memória '%s' (%s): %s", label, path, exc)
            raise MemoryUnavailableError(
                f"Não foi possível ler o arquivo de memória '{label}'."
            ) from exc
