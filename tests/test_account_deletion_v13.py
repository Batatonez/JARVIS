"""Exclusão da conta atual (v1.3, itens 56-59, 69).

A garantia central testada aqui é NEGATIVA: apagar a conta A não pode
encostar na conta B. Todo teste cria duas contas com dados e verifica a
segunda inteira depois.

Banco e diretórios temporários — nenhum dado real é tocado (item 74).
"""

import tempfile
import unittest
from pathlib import Path

from services.account_deletion import (
    AccountDeletionError,
    delete_account,
    user_data_dir,
)
from services.conversation_repository import ConversationRepository
from services.local_database import connect
from services.long_term_memory import LongTermMemoryRepository, MemoryCategory
from services.recovery_code_repository import RecoveryCodeRepository
from services.session_repository import SessionRepository
from services.user_repository import UserRepository
from services.user_settings_repository import KEY_MICROPHONE, UserSettingsRepository
from app.models import Message, MessageRole


class AccountDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.users_dir = self.root / "users"
        self.connection = connect(self.root / "test.db")
        self.addCleanup(self.connection.close)

        self.users = UserRepository(self.connection)
        self.sessions = SessionRepository(self.connection)
        self.conversations = ConversationRepository(self.connection)
        self.memories = LongTermMemoryRepository(self.connection)
        self.recovery = RecoveryCodeRepository(self.connection)
        self.settings = UserSettingsRepository(self.connection)

        self.alice = self._populate("alice", "alice@example.com")
        self.bob = self._populate("bob", "bob@example.com")

    def _populate(self, username, email):
        user = self.users.create_user(
            username=username, display_name=username, password="senha-forte-123", email=email
        )
        self.sessions.create_session(user.id)
        conversation_id = self.conversations.create_conversation(user.id, title=f"Chat de {username}")
        self.conversations.save_message(
            conversation_id, user.id, Message(role=MessageRole.USER, content=f"oi de {username}")
        )
        self.memories.remember(
            user_id=user.id, category=MemoryCategory.IDENTITY, content=f"Nome: {username}"
        )
        self.recovery.generate(user.id)
        self.settings.set(user.id, KEY_MICROPHONE, "WASAPI:Mic")
        self.users.set_totp_secret(user.id, "dpapi:AAAA")
        directory = self.users_dir / user.id
        directory.mkdir(parents=True)
        (directory / "profile.md").write_text(f"# {username}", encoding="utf-8")
        return user

    def _counts(self, user_id):
        return {
            "users": self.connection.execute(
                "SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)
            ).fetchone()[0],
            "sessions": self.connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()[0],
            "conversations": self.connection.execute(
                "SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)
            ).fetchone()[0],
            "messages": self.connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id IN "
                "(SELECT id FROM conversations WHERE user_id = ?)",
                (user_id,),
            ).fetchone()[0],
            "memories": self.connection.execute(
                "SELECT COUNT(*) FROM user_memories WHERE user_id = ?", (user_id,)
            ).fetchone()[0],
            "recovery": self.connection.execute(
                "SELECT COUNT(*) FROM recovery_codes WHERE user_id = ?", (user_id,)
            ).fetchone()[0],
            "settings": self.connection.execute(
                "SELECT COUNT(*) FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()[0],
        }

    # --- item 58: tudo que é da conta some ---

    def test_all_user_scoped_data_is_removed(self) -> None:
        before = self._counts(self.alice.id)
        self.assertTrue(all(value > 0 for value in before.values()), before)

        delete_account(self.connection, user_id=self.alice.id, users_dir=self.users_dir)

        after = self._counts(self.alice.id)
        self.assertEqual(after, dict.fromkeys(after, 0))

    def test_totp_secret_is_gone(self) -> None:
        delete_account(self.connection, user_id=self.alice.id, users_dir=self.users_dir)
        self.assertIsNone(self.users.get_totp_secret(self.alice.id))

    def test_memory_directory_is_removed(self) -> None:
        directory = self.users_dir / self.alice.id
        self.assertTrue(directory.is_dir())
        summary = delete_account(self.connection, user_id=self.alice.id, users_dir=self.users_dir)
        self.assertFalse(directory.exists())
        self.assertTrue(summary.memory_dir_removed)

    # --- item 58: NADA de outra conta é tocado ---

    def test_other_account_survives_completely(self) -> None:
        before = self._counts(self.bob.id)
        delete_account(self.connection, user_id=self.alice.id, users_dir=self.users_dir)
        self.assertEqual(self._counts(self.bob.id), before)
        self.assertIsNotNone(self.users.get_user(self.bob.id))
        self.assertTrue((self.users_dir / self.bob.id / "profile.md").is_file())

    def test_other_account_can_still_log_in(self) -> None:
        delete_account(self.connection, user_id=self.alice.id, users_dir=self.users_dir)
        user = self.users.authenticate(identifier="bob", password="senha-forte-123")
        self.assertEqual(user.id, self.bob.id)

    # --- item 59: transação e path safety ---

    def test_failure_rolls_back_and_preserves_everything(self) -> None:
        """Item 59: falha no meio -> ROLLBACK, e nada é apagado.

        `sqlite3.Connection.execute` é read-only e não aceita `patch.object`,
        então a falha é injetada por um proxy fino em volta da conexão real —
        a transação continua sendo a do SQLite de verdade."""

        class _FailingConnection:
            def __init__(self, inner):
                self._inner = inner
                self.isolation_level = inner.isolation_level

            def execute(self, statement, *args, **kwargs):
                if statement.startswith("DELETE FROM users"):
                    raise RuntimeError("falha simulada no meio da exclusão")
                return self._inner.execute(statement, *args, **kwargs)

        before = self._counts(self.alice.id)
        with self.assertRaises(AccountDeletionError):
            delete_account(
                _FailingConnection(self.connection),
                user_id=self.alice.id,
                users_dir=self.users_dir,
            )

        self.assertEqual(self._counts(self.alice.id), before)
        # Arquivos também: a remoção só acontece DEPOIS do commit.
        self.assertTrue((self.users_dir / self.alice.id / "profile.md").is_file())

    def test_path_traversal_in_user_id_is_refused(self) -> None:
        for evil in ("../outro", "..\\outro", "/etc", "..", ".", "a/b"):
            with self.subTest(evil=evil):
                self.assertIsNone(user_data_dir(self.users_dir, evil))

    def test_valid_user_id_resolves_inside_the_root(self) -> None:
        resolved = user_data_dir(self.users_dir, self.alice.id)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_relative_to(self.users_dir.resolve()))

    def test_empty_user_id_raises(self) -> None:
        with self.assertRaises(AccountDeletionError):
            delete_account(self.connection, user_id="", users_dir=self.users_dir)

    def test_missing_directory_is_not_an_error(self) -> None:
        import shutil

        shutil.rmtree(self.users_dir / self.alice.id)
        summary = delete_account(self.connection, user_id=self.alice.id, users_dir=self.users_dir)
        self.assertFalse(summary.memory_dir_removed)
        self.assertIsNone(self.users.get_user(self.alice.id))


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
