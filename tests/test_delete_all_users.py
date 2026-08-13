"""`jarvis delete all users` — confirmação, backup e exclusão.

**Nenhum teste toca `data/jarvis.db` real**: todos usam `Settings`
apontando para um diretório temporário (`build_isolated_settings`). Nenhum
abre GUI.
"""

import sqlite3
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from app.account_manager import AccountManager
from frontend.cli import Command, main, resolve_command, run_delete_all_users, wants_assume_yes
from services.ai_service import UnavailableAIService
from services.local_database import SCHEMA_VERSION, connect
from services.user_data_reset import BackupFailedError, backup_database, delete_all_users
from tests.fakes import FakeAIService
from tests.fakes_email import FakeEmailService
from tests.helpers import build_isolated_settings, build_isolated_voice_service


async def _settle() -> None:
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0)


class DeleteCommandParsingTests(unittest.TestCase):
    def test_delete_all_users_is_recognized(self) -> None:
        self.assertIs(resolve_command(["delete", "all", "users"]), Command.DELETE_ALL_USERS)

    def test_delete_all_users_with_yes_flag_is_recognized(self) -> None:
        for flag in ("--yes", "-y", "--YES"):
            self.assertIs(
                resolve_command(["delete", "all", "users", flag]), Command.DELETE_ALL_USERS, flag
            )

    def test_delete_is_case_insensitive(self) -> None:
        self.assertIs(resolve_command(["DELETE", "ALL", "USERS"]), Command.DELETE_ALL_USERS)

    def test_partial_delete_commands_are_unknown(self) -> None:
        """"delete" sozinho não pode apagar nada por acidente."""
        for args in (["delete"], ["delete", "all"], ["delete", "users"], ["delete", "everything"]):
            self.assertIs(resolve_command(args), Command.UNKNOWN, f"{args!r}")

    def test_yes_flag_alone_is_not_a_command(self) -> None:
        self.assertIs(resolve_command(["--yes"]), Command.UNKNOWN)

    def test_wants_assume_yes(self) -> None:
        self.assertTrue(wants_assume_yes(["delete", "all", "users", "--yes"]))
        self.assertTrue(wants_assume_yes(["delete", "all", "users", "-y"]))
        self.assertFalse(wants_assume_yes(["delete", "all", "users"]))

    def test_help_mentions_the_delete_command(self) -> None:
        from frontend.cli import HELP_TEXT

        self.assertIn("jarvis delete all users", HELP_TEXT)

    def test_start_commands_still_work(self) -> None:
        """Regressão: adicionar o delete não pode ter quebrado o start."""
        for args in ([], ["wake"], ["wake", "up"], ["start"]):
            self.assertIs(resolve_command(args), Command.START, f"{args!r}")


class _ResetTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.settings = build_isolated_settings(self.tmp_path)

    async def _populate(self) -> AccountManager:
        """Cria uma conta com sessão, conversa, mensagens, memória e um
        token de verificação — para o reset ter o que apagar."""
        account = AccountManager(
            self.settings,
            ai_service_factory=lambda: FakeAIService(available=True, reply="ok"),
            voice_service_factory=build_isolated_voice_service,
            email_service=FakeEmailService(),
        )
        await account.register(
            username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
        )
        await account.request_email_verification(force=True)
        await account.app.send_message("Meu nome é Davi")
        await _settle()
        await account.shutdown()
        return account

    def _counts(self) -> dict[str, int]:
        connection = sqlite3.connect(str(self.settings.db_path))
        try:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "users",
                    "sessions",
                    "conversations",
                    "messages",
                    "user_memories",
                    "email_verification_tokens",
                )
            }
        finally:
            connection.close()


class BackupTests(_ResetTestCase):
    async def test_backup_is_created_before_deleting(self) -> None:
        await self._populate()

        summary = delete_all_users(self.settings)

        self.assertTrue(summary.backup_path.is_file())
        self.assertGreater(summary.backup_path.stat().st_size, 0)
        self.assertIn("jarvis-before-delete-users-", summary.backup_path.name)
        self.assertEqual(summary.backup_path.parent, self.settings.data_dir / "backups")

    async def test_backup_still_contains_the_data_that_was_deleted(self) -> None:
        """O backup só vale se for restaurável — confere que os usuários
        continuam lá dentro depois do banco vivo ter sido esvaziado."""
        await self._populate()

        summary = delete_all_users(self.settings)

        backup = sqlite3.connect(str(summary.backup_path))
        try:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
            self.assertGreater(backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        finally:
            backup.close()

    def test_missing_database_aborts_without_deleting(self) -> None:
        with self.assertRaises(BackupFailedError):
            backup_database(self.tmp_path / "nao-existe.db", self.tmp_path / "backups")

    async def test_backup_failure_deletes_nothing(self) -> None:
        await self._populate()
        before = self._counts()

        with unittest.mock.patch(
            "services.user_data_reset.backup_database", side_effect=BackupFailedError("disco cheio")
        ):
            with self.assertRaises(BackupFailedError):
                delete_all_users(self.settings)

        self.assertEqual(self._counts(), before)  # nada foi apagado


class DeletionTests(_ResetTestCase):
    async def test_all_user_data_tables_are_emptied(self) -> None:
        await self._populate()
        before = self._counts()
        self.assertGreater(before["users"], 0)
        self.assertGreater(before["sessions"], 0)
        self.assertGreater(before["conversations"], 0)
        self.assertGreater(before["messages"], 0)
        self.assertGreater(before["user_memories"], 0)
        self.assertGreater(before["email_verification_tokens"], 0)

        delete_all_users(self.settings)

        after = self._counts()
        for table, count in after.items():
            self.assertEqual(count, 0, f"{table} deveria estar vazia")

    async def test_database_remains_valid_and_migrated(self) -> None:
        """Não apagamos o arquivo do banco — schema e versão continuam."""
        await self._populate()

        delete_all_users(self.settings)

        connection = sqlite3.connect(str(self.settings.db_path))
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(integrity, "ok")
        finally:
            connection.close()

    async def test_app_works_as_a_fresh_install_after_reset(self) -> None:
        await self._populate()
        delete_all_users(self.settings)

        account = AccountManager(
            self.settings,
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
            email_service=FakeEmailService(),
        )
        try:
            # Sem auto-login (a sessão sumiu) e o username volta a estar livre.
            self.assertIsNone(await account.try_auto_login())
            user = await account.register(
                username="alice", display_name="Alice", password="outra-senha-456", email="a@example.com"
            )
            self.assertEqual(user.username, "alice")
            self.assertEqual(account.list_conversations(), [])
            self.assertEqual(account.list_memories(), [])
        finally:
            await account.shutdown()

    async def test_per_user_memory_folders_are_removed(self) -> None:
        account = AccountManager(
            self.settings,
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
            email_service=FakeEmailService(),
        )
        user = await account.register(
            username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
        )
        await account.shutdown()
        user_dir = self.settings.users_dir / user.id
        self.assertTrue(user_dir.is_dir())

        summary = delete_all_users(self.settings)

        self.assertFalse(user_dir.exists())
        self.assertGreaterEqual(summary.memory_dirs_removed, 1)

    async def test_local_session_token_is_cleared(self) -> None:
        await self._populate()
        self.assertTrue(self.settings.session_token_path.is_file())

        delete_all_users(self.settings)

        self.assertFalse(self.settings.session_token_path.is_file())

    async def test_voice_models_and_env_are_preserved(self) -> None:
        """O reset é de CONTAS — não pode levar configuração nem modelos."""
        await self._populate()
        model_dir = self.settings.stt_models_dir / "vosk-model-small-pt"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "final.mdl").write_text("modelo", encoding="utf-8")
        env_file = self.settings.project_root / ".env"
        env_file.write_text("OPENROUTER_API_KEY=fake-para-teste\n", encoding="utf-8")

        delete_all_users(self.settings)

        self.assertTrue((model_dir / "final.mdl").is_file(), "modelo de voz não pode ser apagado")
        self.assertTrue(env_file.is_file(), ".env não pode ser apagado")
        self.assertIn("OPENROUTER_API_KEY", env_file.read_text(encoding="utf-8"))


class DeleteCliFlowTests(_ResetTestCase):
    """A camada de confirmação — a trava contra apagar sem querer."""

    async def test_typing_DELETE_proceeds(self) -> None:
        await self._populate()
        stdout = StringIO()

        with redirect_stdout(stdout):
            exit_code = run_delete_all_users(input_fn=lambda _: "DELETE", settings=self.settings)

        self.assertEqual(exit_code, 0)
        self.assertEqual(self._counts()["users"], 0)
        self.assertIn("Backup saved to:", stdout.getvalue())

    async def test_wrong_confirmation_cancels_and_deletes_nothing(self) -> None:
        await self._populate()
        before = self._counts()

        for answer in ("delete", "yes", "y", "", "DELET", "DELETE ALL"):
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = run_delete_all_users(input_fn=lambda _, a=answer: a, settings=self.settings)

            self.assertEqual(exit_code, 1, f"resposta {answer!r} não deveria prosseguir")
            self.assertIn("Operation cancelled.", stdout.getvalue())

        self.assertEqual(self._counts(), before)  # nada foi apagado

    async def test_confirmation_prompt_warns_before_asking(self) -> None:
        await self._populate()
        stdout = StringIO()

        with redirect_stdout(stdout):
            run_delete_all_users(input_fn=lambda _: "nao", settings=self.settings)

        output = stdout.getvalue()
        self.assertIn("WARNING", output)
        self.assertIn("permanently delete", output)

    async def test_yes_flag_skips_the_prompt(self) -> None:
        await self._populate()
        prompted = []

        def _never(prompt: str) -> str:
            prompted.append(prompt)
            return "DELETE"

        with redirect_stdout(StringIO()):
            exit_code = run_delete_all_users(assume_yes=True, input_fn=_never, settings=self.settings)

        self.assertEqual(exit_code, 0)
        self.assertEqual(prompted, [])  # nunca perguntou
        self.assertEqual(self._counts()["users"], 0)

    async def test_aborted_input_cancels(self) -> None:
        """Ctrl+C / EOF no prompt cancela em vez de apagar."""
        await self._populate()
        before = self._counts()

        def _raise(_prompt: str) -> str:
            raise KeyboardInterrupt()

        with redirect_stdout(StringIO()):
            exit_code = run_delete_all_users(input_fn=_raise, settings=self.settings)

        self.assertEqual(exit_code, 1)
        self.assertEqual(self._counts(), before)

    async def test_backup_failure_is_reported_and_nothing_is_deleted(self) -> None:
        await self._populate()
        before = self._counts()
        stderr = StringIO()

        with unittest.mock.patch(
            "services.user_data_reset.backup_database", side_effect=BackupFailedError("sem espaço")
        ):
            with redirect_stdout(StringIO()), redirect_stderr(stderr):
                exit_code = run_delete_all_users(assume_yes=True, settings=self.settings)

        self.assertEqual(exit_code, 1)
        self.assertIn("Backup failed", stderr.getvalue())
        self.assertIn("Nothing was deleted", stderr.getvalue())
        self.assertEqual(self._counts(), before)

    async def test_main_routes_to_the_delete_flow_without_touching_the_gui(self) -> None:
        with unittest.mock.patch("frontend.launcher.run") as run:
            with unittest.mock.patch("frontend.cli.run_delete_all_users", return_value=0) as delete:
                exit_code = main(["delete", "all", "users"])

        run.assert_not_called()  # nenhuma GUI
        delete.assert_called_once_with(assume_yes=False)
        self.assertEqual(exit_code, 0)

    async def test_main_propagates_the_yes_flag(self) -> None:
        with unittest.mock.patch("frontend.cli.run_delete_all_users", return_value=0) as delete:
            main(["delete", "all", "users", "--yes"])
        delete.assert_called_once_with(assume_yes=True)


class RealDataSafetyTests(unittest.TestCase):
    def test_isolated_settings_never_point_at_the_real_database(self) -> None:
        """Trava explícita: se algum dia `build_isolated_settings` passar a
        vazar o caminho real, estes testes apagariam os dados do usuário."""
        from config.settings import PROJECT_ROOT

        with tempfile.TemporaryDirectory() as tmp:
            settings = build_isolated_settings(Path(tmp))
            real_db = Path(PROJECT_ROOT) / "data" / "jarvis.db"
            self.assertNotEqual(settings.db_path.resolve(), real_db.resolve())
            self.assertTrue(str(settings.db_path).startswith(tmp))
            self.assertTrue(str(settings.users_dir).startswith(tmp))


if __name__ == "__main__":
    unittest.main()
