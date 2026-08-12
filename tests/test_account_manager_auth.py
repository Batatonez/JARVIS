"""Testes de contas/autenticação do `AccountManager` (v0.9) — 10 cenários
pedidos explicitamente: criar usuário, username duplicado, senha errada,
login correto, logout, sessão persistida, sessão inválida, senha nunca em
texto puro, isolamento de token entre usuários, e expiração/remoção de
sessão. Tudo sobre SQLite temporário (`tests/helpers.py`) — nunca toca
`data/`/`memory/` reais do projeto, nem um microfone/TTS real (ver
`build_isolated_voice_service`).
"""

import tempfile
import unittest
from pathlib import Path

from app.account_manager import AccountManager
from services import session_store
from services.ai_service import UnavailableAIService
from services.session_repository import SessionRepository, hash_token
from services.user_repository import InvalidCredentialsError, UsernameAlreadyExistsError
from tests.helpers import build_isolated_account_manager, build_isolated_settings, build_isolated_voice_service


def _account_manager(tmp_path: Path) -> AccountManager:
    return build_isolated_account_manager(
        tmp_path,
        ai_service_factory=UnavailableAIService,
        voice_service_factory=build_isolated_voice_service,
    )


class AccountManagerAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # `ignore_cleanup_errors=True`: no Windows, o SQLite mantém o arquivo
        # aberto até o processo terminar (uma conexão por `AccountManager`,
        # nunca fechada explicitamente em produção — ver app/account_manager.py)
        # — o diretório temporário não pode ser removido no `tearDown`, mas
        # isso não afeta a validade do teste em si.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    # 1. Criar usuário -------------------------------------------------
    async def test_register_creates_user(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            user = await account.register(username="alice", display_name="Alice", password="senha-forte-123")

            self.assertEqual(user.username, "alice")
            self.assertEqual(user.display_name, "Alice")
            self.assertTrue(account.is_authenticated)
            self.assertEqual(account.current_user.id, user.id)
        finally:
            await account.shutdown()

    # 2. Username duplicado ---------------------------------------------
    async def test_register_duplicate_username_raises(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.logout()

            with self.assertRaises(UsernameAlreadyExistsError):
                await account.register(username="alice", display_name="Outra Alice", password="outra-senha-456")
        finally:
            await account.shutdown()

    # 3. Senha errada ------------------------------------------------------
    async def test_login_wrong_password_raises(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.logout()

            with self.assertRaises(InvalidCredentialsError):
                await account.login(username="alice", password="senha-errada")
        finally:
            await account.shutdown()

    # 4. Login correto ------------------------------------------------------
    async def test_login_correct_credentials_returns_user(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.logout()

            user = await account.login(username="alice", password="senha-forte-123")

            self.assertEqual(user.username, "alice")
            self.assertTrue(account.is_authenticated)
        finally:
            await account.shutdown()

    # 5. Logout -------------------------------------------------------------
    async def test_logout_clears_session_and_local_token(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            self.assertTrue(session_store.load_token(account.settings.session_token_path))

            await account.logout()

            self.assertFalse(account.is_authenticated)
            self.assertIsNone(account.current_user)
            self.assertIsNone(session_store.load_token(account.settings.session_token_path))
        finally:
            await account.shutdown()

    # 6. Sessão persistida entre execuções -----------------------------
    async def test_session_persists_across_restart_via_local_token(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        first = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            registered = await first.register(username="alice", display_name="Alice", password="senha-forte-123")
        finally:
            await first.shutdown()  # fecha a sessão de IA, mas preserva o token local (ver docstring de shutdown())

        second = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            user = await second.try_auto_login()

            self.assertIsNotNone(user)
            self.assertEqual(user.id, registered.id)
            self.assertTrue(second.is_authenticated)
        finally:
            await second.shutdown()

    # 7. Sessão inválida ------------------------------------------------
    async def test_auto_login_with_invalid_token_returns_none_and_clears_file(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        session_store.save_token(settings.session_token_path, "token-que-nunca-existiu")

        account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            user = await account.try_auto_login()

            self.assertIsNone(user)
            self.assertFalse(account.is_authenticated)
            self.assertIsNone(session_store.load_token(settings.session_token_path))
        finally:
            await account.shutdown()

    # 8. Senha nunca em texto puro -----------------------------------------
    async def test_password_never_stored_in_plaintext(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            password = "senha-super-secreta-999"
            user = await account.register(username="alice", display_name="Alice", password=password)

            row = account._conn.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user.id,)
            ).fetchone()

            self.assertIsNotNone(row)
            stored_hash = row["password_hash"]
            self.assertNotEqual(stored_hash, password)
            self.assertNotIn(password, stored_hash)
            self.assertTrue(stored_hash.startswith("scrypt$"))
        finally:
            await account.shutdown()

    # 9. Token de um usuário não autentica como outro ------------------
    async def test_session_token_does_not_cross_authenticate_between_users(self) -> None:
        # Duas contas independentes sobre o mesmo banco (mesma `Settings`) —
        # nenhuma faz logout, então nenhum token é invalidado; o que se testa
        # é se o token de uma resolve para a outra por engano.
        settings = build_isolated_settings(self.tmp_path)
        alice_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        bob_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            alice = await alice_account.register(username="alice", display_name="Alice", password="senha-forte-123")
            alice_token = alice_account._current_session_token
            bob = await bob_account.register(username="bob", display_name="Bob", password="outra-senha-456")
            bob_token = bob_account._current_session_token

            self.assertNotEqual(alice.id, bob.id)
            self.assertNotEqual(alice_token, bob_token)

            resolved_for_alice_token = alice_account._sessions.validate_session(alice_token)
            resolved_for_bob_token = alice_account._sessions.validate_session(bob_token)

            self.assertEqual(resolved_for_alice_token, alice.id)
            self.assertEqual(resolved_for_bob_token, bob.id)
            self.assertNotEqual(resolved_for_alice_token, bob.id)
            self.assertNotEqual(resolved_for_bob_token, alice.id)
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()

    # 10. Expiração/remoção de sessão ------------------------------------
    async def test_expired_session_is_rejected_and_removed(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            user = await account.register(username="alice", display_name="Alice", password="senha-forte-123")

            expired_sessions = SessionRepository(account._conn, ttl_days=-1)
            expired_token = expired_sessions.create_session(user.id)

            resolved = account._sessions.validate_session(expired_token)
            self.assertIsNone(resolved)

            # v1.0: `sessions` guarda o SHA-256 do token, nunca o token em si.
            row = account._conn.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ?", (hash_token(expired_token),)
            ).fetchone()
            self.assertIsNone(row)  # validate_session já removeu a sessão vencida
        finally:
            await account.shutdown()


if __name__ == "__main__":
    unittest.main()
