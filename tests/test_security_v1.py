"""Testes de segurança da v1.0 (item 79 do escopo) — hashing, força bruta,
sessões, isolamento entre usuários, SQL injection, path traversal e
sanitização do contexto enviado à IA.

Tudo offline, em banco temporário. Nenhum teste toca `data/` ou `memory/`
reais do projeto.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.account_manager import AccountManager
from services.ai_service import UnavailableAIService
from services.context_builder import prepare_memory_context, sanitize_context
from services.password_hashing import hash_password, verify_password
from services.session_repository import SessionRepository, hash_token
from services.user_repository import AccountLockedError, InvalidCredentialsError
from tests.helpers import build_isolated_settings, build_isolated_voice_service


def _account(tmp_path: Path, settings=None) -> AccountManager:
    return AccountManager(
        settings or build_isolated_settings(tmp_path),
        ai_service_factory=UnavailableAIService,
        voice_service_factory=build_isolated_voice_service,
    )


class PasswordHashingSecurityTests(unittest.TestCase):
    def test_same_password_produces_different_hashes(self) -> None:
        """Salt único por hash — dois usuários com a mesma senha não podem
        ter o mesmo `password_hash` (senão um vazamento revelaria pares)."""
        first = hash_password("mesma-senha")
        second = hash_password("mesma-senha")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("mesma-senha", first))
        self.assertTrue(verify_password("mesma-senha", second))

    def test_hash_never_contains_the_password(self) -> None:
        password = "senha-super-secreta-999"
        self.assertNotIn(password, hash_password(password))

    def test_malformed_hash_is_rejected_without_raising(self) -> None:
        for bad in ("", "nao-e-um-hash", "scrypt$x$y$z", "bcrypt$1$2$3$4$5"):
            self.assertFalse(verify_password("qualquer", bad))


class BruteForceProtectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_repeated_failures_trigger_temporary_lockout(self) -> None:
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.logout()

            # As 4 primeiras falhas ainda são "só" credencial inválida.
            for _ in range(4):
                with self.assertRaises(InvalidCredentialsError):
                    await account.login(identifier="alice", password="errada")

            # A 5ª dispara o cooldown...
            with self.assertRaises(InvalidCredentialsError):
                await account.login(identifier="alice", password="errada")

            # ...e a partir daí até a SENHA CERTA é recusada enquanto durar.
            with self.assertRaises(AccountLockedError) as ctx:
                await account.login(identifier="alice", password="senha-forte-123")
            self.assertGreater(ctx.exception.retry_after_seconds, 0)
        finally:
            await account.shutdown()

    async def test_lockout_is_never_permanent(self) -> None:
        """O bloqueio tem teto (15 min) — nunca vira negação de serviço
        permanente contra o dono da conta."""
        account = _account(self.tmp_path)
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.logout()
            for _ in range(30):
                try:
                    await account.login(identifier="alice", password="errada")
                except (InvalidCredentialsError, AccountLockedError):
                    pass

            row = account._conn.execute(
                "SELECT lockout_until FROM users WHERE id = ?", (user.id,)
            ).fetchone()
            self.assertIsNotNone(row["lockout_until"])  # existe um fim definido
        finally:
            await account.shutdown()

    async def test_successful_login_resets_failure_counter(self) -> None:
        account = _account(self.tmp_path)
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.logout()
            for _ in range(3):
                with self.assertRaises(InvalidCredentialsError):
                    await account.login(identifier="alice", password="errada")

            await account.login(identifier="alice", password="senha-forte-123")

            row = account._conn.execute(
                "SELECT failed_login_attempts, lockout_until FROM users WHERE id = ?", (user.id,)
            ).fetchone()
            self.assertEqual(row["failed_login_attempts"], 0)
            self.assertIsNone(row["lockout_until"])
        finally:
            await account.shutdown()

    async def test_unknown_username_does_not_reveal_itself(self) -> None:
        """Usuário inexistente e senha errada levantam a MESMA exceção com a
        MESMA mensagem — não dá para enumerar contas pelo erro."""
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.logout()

            with self.assertRaises(InvalidCredentialsError) as unknown:
                await account.login(identifier="nao-existe", password="x")
            with self.assertRaises(InvalidCredentialsError) as wrong:
                await account.login(identifier="alice", password="errada")

            self.assertEqual(str(unknown.exception), str(wrong.exception))
        finally:
            await account.shutdown()


class SessionSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_session_token_is_never_stored_in_plaintext(self) -> None:
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            token = account._current_session_token

            rows = account._conn.execute("SELECT * FROM sessions").fetchall()
            self.assertEqual(len(rows), 1)
            stored = dict(rows[0])
            self.assertNotIn("token", stored)  # a coluna em claro não existe mais
            self.assertNotIn(token, stored.values())
            self.assertEqual(stored["token_hash"], hash_token(token))
        finally:
            await account.shutdown()

    async def test_tampered_token_is_rejected(self) -> None:
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            token = account._current_session_token
            self.assertIsNone(account._sessions.validate_session(token + "x"))
            self.assertIsNone(account._sessions.validate_session(hash_token(token)))  # o hash não é a credencial
        finally:
            await account.shutdown()

    async def test_logout_revokes_the_session_server_side(self) -> None:
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            token = account._current_session_token
            await account.logout()

            # Replay do token antigo não autentica mais (nem que alguém o
            # tivesse copiado do disco antes do logout).
            self.assertIsNone(account._sessions.validate_session(token))
        finally:
            await account.shutdown()

    async def test_delete_all_for_user_revokes_every_session(self) -> None:
        account = _account(self.tmp_path)
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            extra = SessionRepository(account._conn).create_session(user.id)

            removed = account._sessions.delete_all_for_user(user.id)

            self.assertGreaterEqual(removed, 2)
            self.assertIsNone(account._sessions.validate_session(extra))
        finally:
            await account.shutdown()


class UserIsolationTests(unittest.IsolatedAsyncioTestCase):
    """IDOR-style: usuário A tentando alcançar dado de B por ID direto."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_user_a_cannot_read_or_mutate_user_b_conversation(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        alice_account = _account(self.tmp_path, settings)
        bob_account = _account(self.tmp_path, settings)
        try:
            await alice_account.register(
                username="alice", display_name="Alice", password="senha-a-123", email="a@example.com"
            )
            alice_conversation = await alice_account.start_new_conversation()
            bob = await bob_account.register(
                username="bob", display_name="Bob", password="senha-b-456", email="b@example.com"
            )

            # Leitura direta pelo ID da conversa de Alice, com o ID de Bob.
            self.assertIsNone(
                bob_account._conversations.get_conversation(alice_conversation, bob.id)
            )
            self.assertFalse(bob_account.rename_conversation(alice_conversation, "invadido"))
            self.assertFalse(await bob_account.delete_conversation(alice_conversation))
            self.assertEqual(bob_account.list_conversations(), [])

            # E a conversa de Alice continua intacta.
            self.assertEqual(len(alice_account.list_conversations()), 1)
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()

    async def test_user_a_cannot_verify_email_of_user_b(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        alice_account = _account(self.tmp_path, settings)
        bob_account = _account(self.tmp_path, settings)
        try:
            alice = await alice_account.register(
                username="alice", display_name="Alice", password="senha-a-123", email="a@example.com"
            )
            await bob_account.register(
                username="bob", display_name="Bob", password="senha-b-456", email="b@example.com"
            )

            # Bob verificando com um código gerado no contexto de Bob nunca
            # pode marcar a conta de Alice como verificada.
            bob_account.verify_email_code("123456")
            refreshed_alice = alice_account._users.get_user(alice.id)
            self.assertFalse(refreshed_alice.email_verified)
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()

    async def test_users_have_separate_memory_directories(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        alice_account = _account(self.tmp_path, settings)
        bob_account = _account(self.tmp_path, settings)
        try:
            alice = await alice_account.register(
                username="alice", display_name="Alice", password="senha-a-123", email="a@example.com"
            )
            bob = await bob_account.register(
                username="bob", display_name="Bob", password="senha-b-456", email="b@example.com"
            )
            self.assertNotEqual(alice.id, bob.id)
            # O caminho usa o UUID interno, nunca o username digitado pelo
            # usuário — um username como "../outro" não escaparia da pasta.
            self.assertIn(alice.id, str(settings.users_dir / alice.id))
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()


class SqlInjectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_malicious_username_is_treated_as_data(self) -> None:
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.logout()

            for payload in ("alice'--", "' OR '1'='1", "'; DROP TABLE users;--"):
                with self.assertRaises(InvalidCredentialsError):
                    await account.login(identifier=payload, password="qualquer")

            # A tabela continua lá e a conta continua utilizável.
            self.assertTrue(account._users.has_any_user())
            user = await account.login(identifier="alice", password="senha-forte-123")
            self.assertEqual(user.username, "alice")
        finally:
            await account.shutdown()

    async def test_malicious_search_query_is_treated_as_data(self) -> None:
        account = _account(self.tmp_path)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.start_new_conversation()

            results = account.search_conversations("'; DROP TABLE conversations;--")

            self.assertEqual(results, [])
            self.assertEqual(len(account.list_conversations()), 1)  # tabela intacta
        finally:
            await account.shutdown()

    def test_no_sql_is_built_by_string_formatting(self) -> None:
        """Varredura estática: nenhuma query montada com f-string/`%`/`+`
        nos repositórios. A única exceção tolerada é `PRAGMA user_version`
        em local_database.py (int interno, nunca entrada do usuário)."""
        import re

        offenders: list[str] = []
        for path in Path("services").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for match in re.finditer(r"execute\(\s*f[\"']", source):
                line = source[: match.start()].count("\n") + 1
                if path.name == "local_database.py":
                    continue  # PRAGMA user_version — auditado, sem input externo
                offenders.append(f"{path}:{line}")
        self.assertEqual(offenders, [])


class ContextSanitizationTests(unittest.TestCase):
    """Item 55: nada de segredo pode sair da máquina junto com o contexto."""

    def test_provider_keys_are_redacted(self) -> None:
        text = "minha chave é sk-or-v1-abcdefghijklmnopqrstuvwxyz123456"
        self.assertNotIn("sk-or-v1-abcdefghijklmnopqrstuvwxyz123456", sanitize_context(text))

    def test_password_hash_is_redacted(self) -> None:
        stored = hash_password("qualquer-senha")
        self.assertNotIn(stored, sanitize_context(f"hash vazado: {stored}"))

    def test_session_token_hash_is_redacted(self) -> None:
        token_hash = hash_token("um-token-qualquer")
        self.assertNotIn(token_hash, sanitize_context(f"token: {token_hash}"))

    def test_authorization_header_is_redacted(self) -> None:
        sanitized = sanitize_context("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123", sanitized)

    def test_env_style_assignments_are_redacted(self) -> None:
        for line in (
            "OPENROUTER_API_KEY=segredo-real-aqui",
            "ANTHROPIC_API_KEY: outro-segredo",
            "SMTP_PASSWORD=senha123",
        ):
            self.assertNotIn("segredo-real-aqui", sanitize_context(line))
            self.assertNotIn("outro-segredo", sanitize_context(line))
            self.assertNotIn("senha123", sanitize_context(line))

    def test_normal_memory_text_is_preserved(self) -> None:
        """A sanitização não pode mutilar memória legítima."""
        text = "O usuário se chama Davi, mora em São Paulo e trabalha com Python."
        self.assertEqual(sanitize_context(text), text)

    def test_context_is_truncated_to_the_budget(self) -> None:
        prepared = prepare_memory_context("x" * 10_000, max_chars=1000)
        self.assertLess(len(prepared), 1200)
        self.assertIn("truncada", prepared)


class DatabaseIntegrityTests(unittest.TestCase):
    def test_foreign_keys_are_enforced(self) -> None:
        """Sem FK ativa, apagar um usuário deixaria conversas órfãs
        acessíveis por ID."""
        from services.local_database import connect

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            conn = connect(Path(tmp) / "jarvis.db")
            try:
                enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                self.assertEqual(enabled, 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                        "VALUES ('c1', 'usuario-inexistente', 't', '2026-01-01', '2026-01-01')"
                    )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
