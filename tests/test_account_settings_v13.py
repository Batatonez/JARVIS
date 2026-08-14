"""Account Settings da v1.3: perfil, senha, troca de e-mail e sessões
(itens 30-32, 41-45, 54-55, 67).

Tudo em banco temporário, com `EmailService` fake — nenhum e-mail real é
enviado (item 74).
"""

import tempfile
import unittest
from pathlib import Path

from app.models import AppErrorCode
from services.account_service import AccountService, PASSWORD_MIN_LENGTH, validate_password
from services.email_change_service import (
    EmailChangeService,
    PendingEmailChangeRepository,
)
from services.local_database import connect
from services.reauth import ReauthGuard
from services.session_repository import SessionRepository
from services.user_repository import UserRepository
from tests.fakes_email import FakeEmailService


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.connection = connect(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.connection.close)

        self.users = UserRepository(self.connection)
        self.sessions = SessionRepository(self.connection)
        self.guard = ReauthGuard()
        self.account = AccountService(self.users, self.sessions, reauth=self.guard)

        self.alice = self.users.create_user(
            username="alice", display_name="Alice", password="senha-forte-123", email="alice@example.com"
        )
        self.bob = self.users.create_user(
            username="bob", display_name="Bob", password="senha-forte-123", email="bob@example.com"
        )


class PasswordPolicyTests(unittest.TestCase):
    def test_minimum_length_is_enforced(self) -> None:
        from services.account_service import InvalidPasswordError

        with self.assertRaises(InvalidPasswordError):
            validate_password("a" * (PASSWORD_MIN_LENGTH - 1))
        self.assertTrue(validate_password("a" * PASSWORD_MIN_LENGTH))

    def test_no_composition_rules(self) -> None:
        """Sem "1 maiúscula, 1 símbolo": regras de composição empurram para
        "Senha1!" e não aumentam entropia real."""
        self.assertTrue(validate_password("cavalo bateria grampo"))


class ReauthIntegrationTests(_Base):
    def test_correct_password_opens_the_window(self) -> None:
        self.assertIsNone(
            self.account.confirm_password(user_id=self.alice.id, password="senha-forte-123")
        )
        self.assertTrue(self.guard.is_valid())

    def test_wrong_password_does_not_open_the_window(self) -> None:
        error = self.account.confirm_password(user_id=self.alice.id, password="errada")
        self.assertEqual(error.code, AppErrorCode.INVALID_PASSWORD)
        self.assertFalse(self.guard.is_valid())

    def test_confirming_password_never_locks_the_account(self) -> None:
        """Confirmar senha numa tela de configurações não é tentativa de
        login: contar como falha permitiria travar a própria conta a partir
        de dentro."""
        for _ in range(10):
            self.account.confirm_password(user_id=self.alice.id, password="errada")
        row = self.connection.execute(
            "SELECT failed_login_attempts, lockout_until FROM users WHERE id = ?", (self.alice.id,)
        ).fetchone()
        self.assertEqual(row["failed_login_attempts"], 0)
        self.assertIsNone(row["lockout_until"])


class ProfileTests(_Base):
    def test_display_name_does_not_require_reauth(self) -> None:
        """Item 32: nome de exibição é só visual."""
        user, error = self.account.change_display_name(user_id=self.alice.id, display_name="Davi C.")
        self.assertIsNone(error)
        self.assertEqual(user.display_name, "Davi C.")

    def test_display_name_is_sanitized_and_limited(self) -> None:
        user, _ = self.account.change_display_name(
            user_id=self.alice.id, display_name="  Davi\n\tCunzolo  " + "x" * 200
        )
        self.assertNotIn("\n", user.display_name)
        self.assertLessEqual(len(user.display_name), 64)

    def test_display_name_can_repeat_across_accounts(self) -> None:
        self.account.change_display_name(user_id=self.alice.id, display_name="Davi")
        user, error = self.account.change_display_name(user_id=self.bob.id, display_name="Davi")
        self.assertIsNone(error)
        self.assertEqual(user.display_name, "Davi")

    def test_username_change_requires_reauth(self) -> None:
        user, error = self.account.change_username(user_id=self.alice.id, username="davi")
        self.assertIsNone(user)
        self.assertEqual(error.code, AppErrorCode.REAUTH_REQUIRED)

    def test_username_change_reports_conflict(self) -> None:
        self.guard.confirm()
        user, error = self.account.change_username(user_id=self.alice.id, username="BOB")
        self.assertIsNone(user)
        self.assertEqual(error.code, AppErrorCode.USERNAME_ALREADY_IN_USE)

    def test_username_change_reports_invalid_format(self) -> None:
        self.guard.confirm()
        user, error = self.account.change_username(user_id=self.alice.id, username="a")
        self.assertIsNone(user)
        self.assertEqual(error.code, AppErrorCode.INVALID_USERNAME)


class ChangePasswordTests(_Base):
    def test_wrong_current_password_is_rejected(self) -> None:
        revoked, error = self.account.change_password(
            user_id=self.alice.id,
            current_password="errada",
            new_password="nova-senha-456",
            confirm_password="nova-senha-456",
        )
        self.assertEqual(error.code, AppErrorCode.INVALID_PASSWORD)
        self.assertEqual(revoked, 0)

    def test_confirmation_must_match(self) -> None:
        _, error = self.account.change_password(
            user_id=self.alice.id,
            current_password="senha-forte-123",
            new_password="nova-senha-456",
            confirm_password="outra-coisa-789",
        )
        self.assertEqual(error.code, AppErrorCode.CONFIRMATION_MISMATCH)

    def test_new_password_must_meet_policy(self) -> None:
        _, error = self.account.change_password(
            user_id=self.alice.id,
            current_password="senha-forte-123",
            new_password="curta",
            confirm_password="curta",
        )
        self.assertEqual(error.code, AppErrorCode.INVALID_PASSWORD)

    def test_successful_change_updates_the_hash(self) -> None:
        before = self.connection.execute(
            "SELECT password_hash FROM users WHERE id = ?", (self.alice.id,)
        ).fetchone()["password_hash"]

        _, error = self.account.change_password(
            user_id=self.alice.id,
            current_password="senha-forte-123",
            new_password="nova-senha-456",
            confirm_password="nova-senha-456",
        )
        self.assertIsNone(error)

        after = self.connection.execute(
            "SELECT password_hash FROM users WHERE id = ?", (self.alice.id,)
        ).fetchone()["password_hash"]
        self.assertNotEqual(before, after)
        self.assertTrue(self.users.verify_password_for(self.alice.id, "nova-senha-456"))
        self.assertFalse(self.users.verify_password_for(self.alice.id, "senha-forte-123"))

    def test_other_sessions_are_revoked_and_current_survives(self) -> None:
        """Item 44."""
        current = self.sessions.create_session(self.alice.id)
        self.sessions.create_session(self.alice.id)
        self.sessions.create_session(self.alice.id)
        bob_token = self.sessions.create_session(self.bob.id)

        revoked, error = self.account.change_password(
            user_id=self.alice.id,
            current_password="senha-forte-123",
            new_password="nova-senha-456",
            confirm_password="nova-senha-456",
            current_token=current,
        )
        self.assertIsNone(error)
        self.assertEqual(revoked, 2)
        self.assertEqual(self.sessions.validate_session(current), self.alice.id)
        # A conta do Bob não foi tocada.
        self.assertEqual(self.sessions.validate_session(bob_token), self.bob.id)

    def test_reauth_window_closes_after_password_change(self) -> None:
        self.guard.confirm()
        self.account.change_password(
            user_id=self.alice.id,
            current_password="senha-forte-123",
            new_password="nova-senha-456",
            confirm_password="nova-senha-456",
        )
        self.assertFalse(self.guard.is_valid())


class SessionListTests(_Base):
    def test_listing_never_exposes_the_token(self) -> None:
        """Item 54."""
        token = self.sessions.create_session(self.alice.id)
        sessions = self.sessions.list_sessions(self.alice.id, current_token=token)
        self.assertEqual(len(sessions), 1)
        blob = repr(sessions[0])
        self.assertNotIn(token, blob)
        from services.session_repository import hash_token

        self.assertNotIn(hash_token(token), blob)

    def test_current_session_is_flagged(self) -> None:
        first = self.sessions.create_session(self.alice.id)
        self.sessions.create_session(self.alice.id)
        sessions = self.sessions.list_sessions(self.alice.id, current_token=first)
        self.assertEqual(sum(1 for s in sessions if s.is_current), 1)

    def test_only_own_sessions_are_listed(self) -> None:
        self.sessions.create_session(self.alice.id)
        self.sessions.create_session(self.bob.id)
        self.assertEqual(len(self.sessions.list_sessions(self.alice.id)), 1)

    def test_log_out_others_requires_reauth(self) -> None:
        token = self.sessions.create_session(self.alice.id)
        revoked, error = self.account.log_out_other_sessions(
            user_id=self.alice.id, current_token=token
        )
        self.assertEqual(error.code, AppErrorCode.REAUTH_REQUIRED)
        self.assertEqual(revoked, 0)

    def test_log_out_others_keeps_current_and_spares_other_users(self) -> None:
        current = self.sessions.create_session(self.alice.id)
        self.sessions.create_session(self.alice.id)
        bob_token = self.sessions.create_session(self.bob.id)

        self.guard.confirm()
        revoked, error = self.account.log_out_other_sessions(
            user_id=self.alice.id, current_token=current
        )
        self.assertIsNone(error)
        self.assertEqual(revoked, 1)
        self.assertEqual(self.sessions.validate_session(current), self.alice.id)
        self.assertEqual(self.sessions.validate_session(bob_token), self.bob.id)

    def test_touch_updates_last_used(self) -> None:
        token = self.sessions.create_session(self.alice.id)
        self.connection.execute(
            "UPDATE sessions SET last_used_at = NULL WHERE user_id = ?", (self.alice.id,)
        )
        self.connection.commit()
        self.sessions.touch(token)
        self.assertIsNotNone(self.sessions.list_sessions(self.alice.id)[0].last_used_at)


class EmailChangeTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.email = FakeEmailService()
        self.service = EmailChangeService(
            PendingEmailChangeRepository(self.connection),
            self.users,
            self.email,
            reauth=self.guard,
        )

    def _last_code(self) -> str:
        return self.email.last_code

    async def test_requires_reauth(self) -> None:
        result = await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        self.assertFalse(result.sent)
        self.assertEqual(result.error.code, AppErrorCode.REAUTH_REQUIRED)

    async def test_rejects_email_already_in_use(self) -> None:
        """Item 42."""
        self.guard.confirm()
        result = await self.service.request_change(user_id=self.alice.id, new_email="BOB@example.com")
        self.assertFalse(result.sent)
        self.assertEqual(result.error.code, AppErrorCode.EMAIL_ALREADY_IN_USE)
        self.assertEqual(len(self.email.sent), 0)

    async def test_rejects_invalid_email(self) -> None:
        self.guard.confirm()
        result = await self.service.request_change(user_id=self.alice.id, new_email="sem-arroba")
        self.assertEqual(result.error.code, AppErrorCode.INVALID_EMAIL)

    async def test_code_goes_to_the_new_address_only(self) -> None:
        self.guard.confirm()
        result = await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        self.assertTrue(result.sent)
        self.assertEqual([m.to for m in self.email.sent], ["novo@example.com"])

    async def test_old_email_stays_active_until_confirmation(self) -> None:
        """Item 41."""
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        self.assertEqual(self.users.get_user(self.alice.id).email, "alice@example.com")

    async def test_wrong_code_does_not_change_the_email(self) -> None:
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        error = await self.service.confirm_change(user_id=self.alice.id, code="000000")
        self.assertEqual(error.code, AppErrorCode.VERIFICATION_CODE_INVALID)
        self.assertEqual(self.users.get_user(self.alice.id).email, "alice@example.com")

    async def test_correct_code_changes_email_and_marks_verified(self) -> None:
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        error = await self.service.confirm_change(user_id=self.alice.id, code=self._last_code())
        self.assertIsNone(error)

        user = self.users.get_user(self.alice.id)
        self.assertEqual(user.email, "novo@example.com")
        self.assertTrue(user.email_verified)

    async def test_previous_address_is_notified_after_success(self) -> None:
        """Item 43."""
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        await self.service.confirm_change(user_id=self.alice.id, code=self._last_code())
        self.assertEqual(self.email.sent[-1].to, "alice@example.com")

    async def test_notification_failure_does_not_undo_the_change(self) -> None:
        """Item 43: o aviso é informativo; falhar nele não pode reverter uma
        troca já concluída e já provada."""
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        code = self._last_code()
        self.email._fail = True  # aviso ao endereço antigo vai falhar
        error = await self.service.confirm_change(user_id=self.alice.id, code=code)
        self.assertIsNone(error)
        self.assertEqual(self.users.get_user(self.alice.id).email, "novo@example.com")

    async def test_too_many_attempts_are_blocked(self) -> None:
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        for _ in range(5):
            await self.service.confirm_change(user_id=self.alice.id, code="000000")
        error = await self.service.confirm_change(user_id=self.alice.id, code="000000")
        self.assertEqual(error.code, AppErrorCode.VERIFICATION_TOO_MANY_ATTEMPTS)

    async def test_resend_respects_cooldown(self) -> None:
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        result = await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        self.assertFalse(result.sent)
        self.assertEqual(result.error.code, AppErrorCode.VERIFICATION_RESEND_TOO_SOON)

    async def test_new_request_invalidates_the_previous_code(self) -> None:
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="um@example.com")
        old_code = self._last_code()
        await self.service.request_change(
            user_id=self.alice.id, new_email="dois@example.com", force=True
        )
        error = await self.service.confirm_change(user_id=self.alice.id, code=old_code)
        self.assertIsNotNone(error)

    async def test_race_condition_is_caught_by_the_database(self) -> None:
        """Item 36: alguém registra o e-mail entre o pedido e a confirmação."""
        self.guard.confirm()
        await self.service.request_change(user_id=self.alice.id, new_email="novo@example.com")
        code = self._last_code()
        self.users.set_email(self.bob.id, "novo@example.com")

        error = await self.service.confirm_change(user_id=self.alice.id, code=code)
        self.assertEqual(error.code, AppErrorCode.EMAIL_ALREADY_IN_USE)
        self.assertEqual(self.users.get_user(self.alice.id).email, "alice@example.com")


if __name__ == "__main__":
    unittest.main()
