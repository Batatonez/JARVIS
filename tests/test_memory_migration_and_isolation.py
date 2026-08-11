"""Testes de memória por conta (v0.9): isolamento entre usuários e migração
controlada de memória legacy (`memory/profile.md`/`memory/preferences.md`,
pré-contas) para a primeira conta criada no ambiente. Usa sempre arquivos de
fixture em diretório temporário — nunca `config.settings.settings` real nem
`memory/` do projeto (ver services/memory_migration.py e o requisito
explícito do pedido: testes de migração nunca tocam a memória real do dono
do projeto).
"""

import tempfile
import unittest
from pathlib import Path

from config.settings import PROJECT_ROOT
from services import memory_migration
from services.ai_service import UnavailableAIService
from services.memory_service import MemoryService
from tests.helpers import build_isolated_account_manager, build_isolated_settings, build_isolated_voice_service


class MemoryMigrationTests(unittest.TestCase):
    """`services.memory_migration` isolado — sem AccountManager, sem asyncio."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_migrates_existing_legacy_files_without_deleting_originals(self) -> None:
        legacy_profile = self.tmp_path / "legacy" / "profile.md"
        legacy_preferences = self.tmp_path / "legacy" / "preferences.md"
        legacy_profile.parent.mkdir(parents=True, exist_ok=True)
        legacy_profile.write_text("# Perfil legacy de teste", encoding="utf-8")
        legacy_preferences.write_text("# Preferências legacy de teste", encoding="utf-8")

        target_dir = self.tmp_path / "users" / "user-1" / "memory"
        migrated = memory_migration.migrate_legacy_memory(
            legacy_profile_path=legacy_profile,
            legacy_preferences_path=legacy_preferences,
            target_memory_dir=target_dir,
        )

        self.assertEqual(sorted(migrated), ["preferences.md", "profile.md"])
        self.assertEqual((target_dir / "profile.md").read_text(encoding="utf-8"), "# Perfil legacy de teste")
        self.assertEqual(
            (target_dir / "preferences.md").read_text(encoding="utf-8"), "# Preferências legacy de teste"
        )
        # Original nunca é apagado nem movido.
        self.assertTrue(legacy_profile.is_file())
        self.assertTrue(legacy_preferences.is_file())

    def test_does_not_overwrite_existing_account_memory(self) -> None:
        legacy_profile = self.tmp_path / "legacy" / "profile.md"
        legacy_profile.parent.mkdir(parents=True, exist_ok=True)
        legacy_profile.write_text("# Perfil legacy", encoding="utf-8")

        target_dir = self.tmp_path / "users" / "user-1" / "memory"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "profile.md").write_text("# Perfil já escrito pela própria conta", encoding="utf-8")

        migrated = memory_migration.migrate_legacy_memory(
            legacy_profile_path=legacy_profile,
            legacy_preferences_path=self.tmp_path / "legacy" / "preferences.md",  # não existe
            target_memory_dir=target_dir,
        )

        self.assertEqual(migrated, [])
        self.assertEqual(
            (target_dir / "profile.md").read_text(encoding="utf-8"), "# Perfil já escrito pela própria conta"
        )

    def test_no_legacy_files_migrates_nothing(self) -> None:
        migrated = memory_migration.migrate_legacy_memory(
            legacy_profile_path=self.tmp_path / "nao-existe" / "profile.md",
            legacy_preferences_path=self.tmp_path / "nao-existe" / "preferences.md",
            target_memory_dir=self.tmp_path / "users" / "user-1" / "memory",
        )
        self.assertEqual(migrated, [])

    def test_never_touches_real_project_memory(self) -> None:
        """Requisito explícito do pedido: testes de migração usam sempre
        fixtures temporárias — a memória real do dono do projeto
        (`<repo>/memory/profile.md`/`preferences.md`) precisa continuar
        byte-a-byte idêntica depois de qualquer teste desta suíte rodar."""
        real_profile = PROJECT_ROOT / "memory" / "profile.md"
        real_preferences = PROJECT_ROOT / "memory" / "preferences.md"
        before = {
            path: path.read_bytes() if path.is_file() else None for path in (real_profile, real_preferences)
        }

        legacy_profile = self.tmp_path / "legacy" / "profile.md"
        legacy_profile.parent.mkdir(parents=True, exist_ok=True)
        legacy_profile.write_text("# Fixture, não a memória real", encoding="utf-8")
        memory_migration.migrate_legacy_memory(
            legacy_profile_path=legacy_profile,
            legacy_preferences_path=self.tmp_path / "legacy" / "preferences.md",
            target_memory_dir=self.tmp_path / "users" / "user-1" / "memory",
        )

        after = {
            path: path.read_bytes() if path.is_file() else None for path in (real_profile, real_preferences)
        }
        self.assertEqual(before, after)


class AccountMemoryIsolationTests(unittest.IsolatedAsyncioTestCase):
    """Memória por conta através do `AccountManager` de ponta a ponta —
    cada usuário lê/escreve só a própria pasta (`data/users/<id>/memory/`)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _account_manager(self):
        return build_isolated_account_manager(
            self.tmp_path,
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
        )

    async def test_each_user_gets_a_separate_memory_directory(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        from app.account_manager import AccountManager

        alice_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        bob_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            alice = await alice_account.register(username="alice", display_name="Alice", password="senha-forte-123")
            bob = await bob_account.register(username="bob", display_name="Bob", password="outra-senha-456")

            alice_dir = memory_migration.user_memory_dir(settings.users_dir, alice.id)
            bob_dir = memory_migration.user_memory_dir(settings.users_dir, bob.id)
            self.assertNotEqual(alice_dir, bob_dir)

            (alice_dir / "profile.md").write_text("# Perfil da Alice", encoding="utf-8")
            (bob_dir / "profile.md").write_text("# Perfil do Bob", encoding="utf-8")

            alice_memory = MemoryService(alice_dir / "profile.md", alice_dir / "preferences.md")
            bob_memory = MemoryService(bob_dir / "profile.md", bob_dir / "preferences.md")

            self.assertEqual(alice_memory.get_profile(), "# Perfil da Alice")
            self.assertEqual(bob_memory.get_profile(), "# Perfil do Bob")
            self.assertNotEqual(alice_memory.get_profile(), bob_memory.get_profile())
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()

    async def test_first_account_created_triggers_legacy_migration(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        # Fixture de memória legacy — nunca a memória real do projeto (ver
        # `test_never_touches_real_project_memory` acima).
        settings.profile_path.parent.mkdir(parents=True, exist_ok=True)
        settings.profile_path.write_text("# Perfil legacy de fixture", encoding="utf-8")
        settings.preferences_path.write_text("# Preferências legacy de fixture", encoding="utf-8")

        from app.account_manager import AccountManager

        account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            user = await account.register(username="alice", display_name="Alice", password="senha-forte-123")

            user_dir = memory_migration.user_memory_dir(settings.users_dir, user.id)
            self.assertEqual((user_dir / "profile.md").read_text(encoding="utf-8"), "# Perfil legacy de fixture")
            self.assertEqual(
                (user_dir / "preferences.md").read_text(encoding="utf-8"), "# Preferências legacy de fixture"
            )
            # Fixture legacy preservada (nunca movida/apagada).
            self.assertTrue(settings.profile_path.is_file())
        finally:
            await account.shutdown()

    async def test_second_account_does_not_receive_legacy_migration(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        settings.profile_path.parent.mkdir(parents=True, exist_ok=True)
        settings.profile_path.write_text("# Perfil legacy de fixture", encoding="utf-8")

        from app.account_manager import AccountManager

        first_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        second_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            await first_account.register(username="alice", display_name="Alice", password="senha-forte-123")
            bob = await second_account.register(username="bob", display_name="Bob", password="outra-senha-456")

            bob_dir = memory_migration.user_memory_dir(settings.users_dir, bob.id)
            self.assertFalse((bob_dir / "profile.md").exists())
        finally:
            await first_account.shutdown()
            await second_account.shutdown()


if __name__ == "__main__":
    unittest.main()
