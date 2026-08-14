"""Identidade única e autenticação da v1.3 (itens 33-40, 66-67).

O item 66 é marcado como CRÍTICO no escopo, então os três casos exigidos
estão aqui explicitamente:

    caso 1  duas contas com o mesmo e-mail        -> falha
    caso 2  mesmo e-mail com caixa diferente      -> falha
    caso 3  trocar para o e-mail de outra conta   -> falha

E a constraint do BANCO é testada separadamente da checagem do serviço: se
alguém remover a validação em Python, o índice UNIQUE ainda precisa barrar.

Tudo em banco temporário. Nenhum teste toca `data/jarvis.db`.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.local_database import (
    IdentityConflictError,
    connect,
    find_identity_conflicts,
    migrate,
)
from services.user_repository import (
    AccountLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidUsernameError,
    UserRepository,
    UsernameAlreadyExistsError,
    normalize_email,
    normalize_username,
    validate_username,
)


class _TempDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.connection = connect(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.connection.close)
        self.users = UserRepository(self.connection)

    def _create(self, username, email, password="senha-forte-123"):
        return self.users.create_user(
            username=username, display_name=username.title(), password=password, email=email
        )


# ----------------------------------------------------------------------
# Item 66 — e-mail único (CRÍTICO)
# ----------------------------------------------------------------------


class UniqueEmailTests(_TempDatabase):
    def test_case_1_second_account_with_same_email_fails(self) -> None:
        self._create("alice", "teste@example.com")
        with self.assertRaises(EmailAlreadyRegisteredError):
            self._create("bob", "teste@example.com")

    def test_case_2_same_email_different_case_fails(self) -> None:
        self._create("alice", "TESTE@example.com")
        with self.assertRaises(EmailAlreadyRegisteredError):
            self._create("bob", "teste@example.com")

    def test_case_3_changing_to_another_accounts_email_fails(self) -> None:
        alice = self._create("alice", "alice@example.com")
        bob = self._create("bob", "bob@example.com")
        with self.assertRaises(EmailAlreadyRegisteredError):
            self.users.set_email(bob.id, "ALICE@example.com")
        # Nada mudou na conta do Bob.
        self.assertEqual(self.users.get_user(bob.id).email, "bob@example.com")

    def test_database_constraint_blocks_even_bypassing_the_service(self) -> None:
        """Item 36: a garantia final é do banco, não do Python."""
        self._create("alice", "alice@example.com")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO users (id, username, normalized_username, display_name, "
                "password_hash, plan, created_at, email, normalized_email, email_verified) "
                "VALUES ('x', 'eve', 'eve', 'Eve', 'h', 'free', '2026-01-01', "
                "'Alice@Example.com', 'alice@example.com', 0)"
            )

    def test_email_error_message_does_not_reveal_the_owner(self) -> None:
        """Item 36/39: não enumerar contas alheias."""
        self._create("alice", "alice@example.com")
        with self.assertRaises(EmailAlreadyRegisteredError) as ctx:
            self._create("bob", "alice@example.com")
        self.assertNotIn("alice", str(ctx.exception).lower())

    def test_error_carries_structured_code(self) -> None:
        self.assertEqual(EmailAlreadyRegisteredError.code, "EMAIL_ALREADY_IN_USE")

    def test_same_user_can_keep_their_own_email(self) -> None:
        alice = self._create("alice", "alice@example.com")
        self.assertFalse(self.users.email_in_use("alice@example.com", excluding_user_id=alice.id))
        self.assertTrue(self.users.email_in_use("alice@example.com"))

    def test_partial_creation_never_happens(self) -> None:
        """Item 37: e-mail duplicado não pode deixar o username reservado."""
        self._create("alice", "alice@example.com")
        with self.assertRaises(EmailAlreadyRegisteredError):
            self._create("carol", "ALICE@example.com")
        self.assertIsNone(self.users.find_by_username("carol"))


class UniqueUsernameTests(_TempDatabase):
    def test_case_insensitive_conflict(self) -> None:
        """Item 33: "BatatoNez" e "batatonez" são a mesma conta."""
        self._create("BatatoNez", "a@example.com")
        with self.assertRaises(UsernameAlreadyExistsError):
            self._create("batatonez", "b@example.com")

    def test_original_case_is_preserved_for_display(self) -> None:
        user = self._create("BatatoNez", "a@example.com")
        self.assertEqual(user.username, "BatatoNez")
        self.assertEqual(normalize_username(user.username), "batatonez")

    def test_charset_and_length_rules(self) -> None:
        for invalid in ("ab", "a" * 33, "com espaço", "-comeca-com-hifen", "acentuação", "emoji🙂"):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidUsernameError):
                validate_username(invalid)

    def test_control_characters_are_stripped(self) -> None:
        self.assertEqual(validate_username("ali​ce"), "alice")

    def test_valid_usernames_are_accepted(self) -> None:
        for valid in ("alice", "BatatoNez", "user_1", "a.b-c", "davi123"):
            with self.subTest(valid=valid):
                self.assertTrue(validate_username(valid))


class EmailNormalizationTests(unittest.TestCase):
    def test_trims_and_lowercases(self) -> None:
        self.assertEqual(normalize_email("  DAVI@Gmail.COM "), "davi@gmail.com")

    def test_does_not_invent_provider_rules(self) -> None:
        """Item 35: NÃO remover pontos do Gmail nem sub-endereçamento —
        `d.a.v.i@gmail.com` e `davi+jarvis@gmail.com` são endereços
        legítimos e distintos para um servidor genérico."""
        self.assertEqual(normalize_email("d.a.v.i@gmail.com"), "d.a.v.i@gmail.com")
        self.assertEqual(normalize_email("davi+jarvis@gmail.com"), "davi+jarvis@gmail.com")

    def test_invalid_shapes_are_rejected(self) -> None:
        from services.user_repository import validate_email

        for invalid in ("", "sem-arroba", "a@b", "a@@b.com", "a b@c.com", "x" * 250 + "@e.com"):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidEmailError):
                validate_email(invalid)


# ----------------------------------------------------------------------
# Item 67 — login por username OU e-mail
# ----------------------------------------------------------------------


class LoginTests(_TempDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = self._create("BatatoNez", "Davi@Example.com")

    def test_login_by_username(self) -> None:
        user = self.users.authenticate(identifier="BatatoNez", password="senha-forte-123")
        self.assertEqual(user.id, self.alice.id)

    def test_login_by_username_case_insensitive(self) -> None:
        user = self.users.authenticate(identifier="batatonez", password="senha-forte-123")
        self.assertEqual(user.id, self.alice.id)

    def test_login_by_email(self) -> None:
        user = self.users.authenticate(identifier="Davi@Example.com", password="senha-forte-123")
        self.assertEqual(user.id, self.alice.id)

    def test_login_by_email_case_insensitive(self) -> None:
        user = self.users.authenticate(identifier="DAVI@EXAMPLE.COM", password="senha-forte-123")
        self.assertEqual(user.id, self.alice.id)

    def test_wrong_password_fails(self) -> None:
        with self.assertRaises(InvalidCredentialsError):
            self.users.authenticate(identifier="BatatoNez", password="errada")

    def test_unknown_account_and_wrong_password_are_indistinguishable(self) -> None:
        """Item 39: mesma exceção e mesma mensagem."""
        with self.assertRaises(InvalidCredentialsError) as unknown:
            self.users.authenticate(identifier="nao-existe", password="qualquer")
        with self.assertRaises(InvalidCredentialsError) as wrong:
            self.users.authenticate(identifier="BatatoNez", password="errada")
        self.assertEqual(str(unknown.exception), str(wrong.exception))

    def test_empty_identifier_is_rejected(self) -> None:
        with self.assertRaises(InvalidCredentialsError):
            self.users.authenticate(identifier="   ", password="senha-forte-123")

    def test_brute_force_backoff_still_applies(self) -> None:
        for _ in range(5):
            with self.assertRaises(InvalidCredentialsError):
                self.users.authenticate(identifier="BatatoNez", password="errada")
        with self.assertRaises(AccountLockedError) as ctx:
            self.users.authenticate(identifier="BatatoNez", password="senha-forte-123")
        self.assertGreater(ctx.exception.retry_after_seconds, 0)

    def test_lockout_is_never_permanent(self) -> None:
        for _ in range(20):
            try:
                self.users.authenticate(identifier="BatatoNez", password="errada")
            except (InvalidCredentialsError, AccountLockedError):
                pass
        row = self.connection.execute(
            "SELECT lockout_until FROM users WHERE id = ?", (self.alice.id,)
        ).fetchone()
        self.assertIsNotNone(row["lockout_until"])  # tem prazo, não é "para sempre"

    def test_sql_injection_in_identifier_is_treated_as_data(self) -> None:
        for payload in ("' OR '1'='1", "admin'--", "'; DROP TABLE users;--"):
            with self.subTest(payload=payload), self.assertRaises(InvalidCredentialsError):
                self.users.authenticate(identifier=payload, password="qualquer")
        self.assertIsNotNone(self.users.get_user(self.alice.id))


# ----------------------------------------------------------------------
# Item 40 — trocar username não quebra nada
# ----------------------------------------------------------------------


class ChangeUsernameTests(_TempDatabase):
    def test_user_id_is_stable_across_rename(self) -> None:
        alice = self._create("alice", "a@example.com")
        updated = self.users.update_username(alice.id, "DaviNovo")
        self.assertEqual(updated.id, alice.id)
        self.assertEqual(updated.username, "DaviNovo")

    def test_login_uses_the_new_username(self) -> None:
        alice = self._create("alice", "a@example.com")
        self.users.update_username(alice.id, "davi")
        self.assertEqual(
            self.users.authenticate(identifier="davi", password="senha-forte-123").id, alice.id
        )
        with self.assertRaises(InvalidCredentialsError):
            self.users.authenticate(identifier="alice", password="senha-forte-123")

    def test_conflicting_username_is_rejected(self) -> None:
        self._create("alice", "a@example.com")
        bob = self._create("bob", "b@example.com")
        with self.assertRaises(UsernameAlreadyExistsError):
            self.users.update_username(bob.id, "ALICE")
        self.assertEqual(self.users.get_user(bob.id).username, "bob")

    def test_related_data_still_points_to_the_account(self) -> None:
        """Chats/memória/sessões referenciam `user_id`, nunca o username."""
        from services.conversation_repository import ConversationRepository

        alice = self._create("alice", "a@example.com")
        conversations = ConversationRepository(self.connection)
        conversation_id = conversations.create_conversation(alice.id, title="Antes")
        self.users.update_username(alice.id, "davi")
        self.assertEqual(
            [c.id for c in conversations.list_conversations(alice.id)], [conversation_id]
        )


# ----------------------------------------------------------------------
# Item 62 — duplicatas legadas param a migração
# ----------------------------------------------------------------------


class LegacyConflictTests(unittest.TestCase):
    """A migração 4 adiciona UNIQUE em `normalized_email`. Se o banco já
    tiver duplicatas, o item 62 exige PARAR e reportar — nunca apagar, fundir
    ou reescrever o e-mail de alguém."""

    def _v3_database(self, path: Path, rows):
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # Migra até a v3 e para lá.
        import services.local_database as db

        with unittest.mock.patch.object(db, "SCHEMA_VERSION", 3):
            migrate(connection)
        for index, (username, email) in enumerate(rows):
            connection.execute(
                "INSERT INTO users (id, username, display_name, password_hash, plan, "
                "created_at, email, email_verified) VALUES (?, ?, ?, 'h', 'free', '2026-01-01', ?, 0)",
                (f"id-{index}", username, username, email),
            )
        connection.commit()
        return connection

    def test_duplicate_emails_abort_the_migration_and_preserve_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            connection = self._v3_database(
                path, [("alice", "Teste@example.com"), ("bob", "teste@example.com")]
            )
            try:
                with self.assertRaises(IdentityConflictError) as ctx:
                    migrate(connection)
                self.assertIn("teste@example.com", str(ctx.exception))
                # NADA foi apagado.
                total = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                self.assertEqual(total, 2)
            finally:
                connection.close()

    def test_conflict_report_is_read_only_and_lists_offenders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._v3_database(
                Path(tmp) / "legacy.db",
                [("alice", "Teste@example.com"), ("bob", "teste@example.com")],
            )
            try:
                conflicts = find_identity_conflicts(connection)
                self.assertEqual(conflicts["email"], [("teste@example.com", 2)])
                self.assertEqual(conflicts["username"], [])
            finally:
                connection.close()

    def test_clean_legacy_database_migrates_and_backfills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            connection = self._v3_database(
                Path(tmp) / "legacy.db", [("Alice", "Alice@example.com"), ("bob", None)]
            )
            try:
                migrate(connection)
                rows = connection.execute(
                    "SELECT username, normalized_username, email, normalized_email FROM users "
                    "ORDER BY username"
                ).fetchall()
                self.assertEqual(rows[0]["normalized_username"], "alice")
                self.assertEqual(rows[0]["normalized_email"], "alice@example.com")
                # Conta legacy sem e-mail continua válida e não colide.
                self.assertIsNone(rows[1]["normalized_email"])
            finally:
                connection.close()


import unittest.mock  # noqa: E402  (usado por LegacyConflictTests)

if __name__ == "__main__":
    unittest.main()
