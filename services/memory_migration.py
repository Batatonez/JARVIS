"""Migração controlada de `memory/profile.md`/`memory/preferences.md`
(memória legacy, pré-contas — v0.1-v0.8) para a memória da primeira conta
criada neste ambiente (`data/users/<user-id>/memory/`).

Regras (ver docs/architecture.md, seção Contas):
- Nunca move nem apaga os arquivos legacy — sempre copia.
- Nunca sobrescreve memória que a conta já tenha (idempotente: só roda
  quando a pasta de memória do usuário ainda não tem nada).
- Só acontece para a PRIMEIRA conta criada no ambiente (`AccountManager`
  decide isso via `UserRepository.has_any_user()` antes de criar a conta —
  este módulo só executa a cópia em si, sem saber "sou a primeira conta").
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def user_memory_dir(users_dir: Path, user_id: str) -> Path:
    return Path(users_dir) / user_id / "memory"


def migrate_legacy_memory(
    *, legacy_profile_path: Path, legacy_preferences_path: Path, target_memory_dir: Path
) -> list[str]:
    """Copia os arquivos legacy que existirem para `target_memory_dir`, sem
    sobrescrever nada que já esteja lá. Retorna os nomes de arquivo
    efetivamente copiados (lista vazia se não havia nada a migrar ou se o
    destino já tinha conteúdo)."""
    target_memory_dir = Path(target_memory_dir)
    migrated: list[str] = []

    for legacy_path, filename in (
        (Path(legacy_profile_path), "profile.md"),
        (Path(legacy_preferences_path), "preferences.md"),
    ):
        destination = target_memory_dir / filename
        if destination.exists():
            continue  # já migrado (ou a conta já tem seu próprio arquivo) — nunca sobrescreve
        if not legacy_path.is_file():
            continue  # nada legacy para copiar
        target_memory_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy_path, destination)
        migrated.append(filename)
        logger.info("Memória legacy migrada: %s -> %s (original preservado)", legacy_path, destination)

    return migrated
