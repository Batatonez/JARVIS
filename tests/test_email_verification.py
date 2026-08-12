"""Verificação de e-mail (v1.0) — os 12 cenários do escopo, todos com
`FakeEmailService`. Nenhum e-mail real é enviado em nenhum momento
(item 81); nenhum teste toca rede.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.account_manager import AccountManager
from app.models import AppErrorCode
from services.ai_service import UnavailableAIService
from services.email_verification_repository import (
    EmailVerificationRepository,
    MAX_ATTEMPTS,
    RESEND_COOLDOWN_SECONDS,
)
from services.email_verification_service import generate_code, mask_email
from services.user_repository import EmailAlreadyRegisteredError
from tests.fakes_email import FakeEmailService
from tests.helpers import build_isolated_settings, build_isolated_voice_service


class EmailVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.mailer = FakeEmailService()

    def _account(self, *, mailer: FakeEmailService | None = None, settings=None) -> AccountManager:
        return AccountManager(
            settings or build_isolated_settings(self.tmp_path),
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
            email_service=mailer or self.mailer,
        )

    async def _registered(self, account: AccountManager, *, email: str = "alice@example.com"):
        return await account.register(
            username="alice", display_name="Alice", password="senha-forte-123", email=email
        )

    # 1. cadastro cria a conta com e-mail, não verificada -------------------
    async def test_registration_stores_email_unverified(self) -> None:
        account = self._account()
        try:
            user = await self._registered(account)
            self.assertEqual(user.email, "alice@example.com")
            self.assertFalse(user.email_verified)
        finally:
            await account.shutdown()

    # 2. token criado e enviado --------------------------------------------
    async def test_request_code_creates_challenge_and_sends_email(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            result = await account.request_email_verification(force=True)

            self.assertTrue(result.sent)
            self.assertIsNotNone(result.challenge)
            self.assertEqual(len(self.mailer.sent), 1)
            self.assertEqual(self.mailer.sent[0].to, "alice@example.com")
            self.assertIsNotNone(self.mailer.last_code)
        finally:
            await account.shutdown()

    # 3. token correto verifica --------------------------------------------
    async def test_correct_code_verifies_account(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            await account.request_email_verification(force=True)

            error = account.verify_email_code(self.mailer.last_code)

            self.assertIsNone(error)
            self.assertTrue(account.current_user.email_verified)
        finally:
            await account.shutdown()

    # 4. token errado ------------------------------------------------------
    async def test_wrong_code_is_rejected(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            await account.request_email_verification(force=True)
            wrong = "000000" if self.mailer.last_code != "000000" else "111111"

            error = account.verify_email_code(wrong)

            self.assertIsNotNone(error)
            self.assertEqual(error.code, AppErrorCode.VERIFICATION_CODE_INVALID)
            self.assertFalse(account.current_user.email_verified)
        finally:
            await account.shutdown()

    # 5. token expirado ----------------------------------------------------
    async def test_expired_code_is_rejected(self) -> None:
        account = self._account()
        try:
            user = await self._registered(account)
            await account.request_email_verification(force=True)
            code = self.mailer.last_code

            # Envelhece o desafio direto no banco (o service valida contra o
            # relógio real, então não há como "esperar" 5 minutos num teste).
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            account._conn.execute(
                "UPDATE email_verification_tokens SET expires_at = ? WHERE user_id = ?", (past, user.id)
            )
            account._conn.commit()

            error = account.verify_email_code(code)

            self.assertIsNotNone(error)
            self.assertEqual(error.code, AppErrorCode.VERIFICATION_CODE_EXPIRED)
        finally:
            await account.shutdown()

    # 6. reuso do token ----------------------------------------------------
    async def test_code_cannot_be_reused(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            await account.request_email_verification(force=True)
            code = self.mailer.last_code

            self.assertIsNone(account.verify_email_code(code))
            second = account.verify_email_code(code)

            self.assertIsNotNone(second)
            self.assertEqual(second.code, AppErrorCode.VERIFICATION_CODE_INVALID)
        finally:
            await account.shutdown()

    # 7. reenvio antes de 60s é recusado ------------------------------------
    async def test_resend_before_cooldown_is_rejected(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            await account.request_email_verification(force=True)
            self.assertEqual(len(self.mailer.sent), 1)

            result = await account.request_email_verification(force=False)

            self.assertFalse(result.sent)
            self.assertEqual(result.error.code, AppErrorCode.VERIFICATION_RESEND_TOO_SOON)
            self.assertEqual(len(self.mailer.sent), 1)  # nada foi enviado de novo
        finally:
            await account.shutdown()

    # 8. reenvio depois de 60s é permitido ---------------------------------
    async def test_resend_after_cooldown_is_allowed(self) -> None:
        account = self._account()
        try:
            user = await self._registered(account)
            await account.request_email_verification(force=True)

            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            account._conn.execute(
                "UPDATE email_verification_tokens SET resend_available_at = ? WHERE user_id = ?",
                (past, user.id),
            )
            account._conn.commit()

            result = await account.request_email_verification(force=False)

            self.assertTrue(result.sent)
            self.assertEqual(len(self.mailer.sent), 2)
        finally:
            await account.shutdown()

    # 9. novo código invalida o antigo --------------------------------------
    async def test_new_code_invalidates_previous_one(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            await account.request_email_verification(force=True)
            old_code = self.mailer.last_code

            await account.request_email_verification(force=True)
            new_code = self.mailer.last_code
            self.assertNotEqual(old_code, new_code)

            error = account.verify_email_code(old_code)
            self.assertIsNotNone(error)  # o antigo não vale mais

            self.assertIsNone(account.verify_email_code(new_code))
        finally:
            await account.shutdown()

    # 10. e-mail duplicado --------------------------------------------------
    async def test_duplicate_email_is_rejected(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        first = self._account(settings=settings)
        second = self._account(settings=settings, mailer=FakeEmailService())
        try:
            await self._registered(first)
            with self.assertRaises(EmailAlreadyRegisteredError):
                await second.register(
                    username="bob", display_name="Bob", password="outra-senha", email="ALICE@example.com"
                )
        finally:
            await first.shutdown()
            await second.shutdown()

    # 11. estado sobrevive a um restart -------------------------------------
    async def test_challenge_survives_restart_with_real_remaining_time(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        first = self._account(settings=settings)
        try:
            await self._registered(first)
            await first.request_email_verification(force=True)
            code = self.mailer.last_code
            remaining_before = first.active_verification_challenge().seconds_until_expiry()
        finally:
            await first.shutdown()

        second = self._account(settings=settings, mailer=self.mailer)
        try:
            user = await second.login(username="alice", password="senha-forte-123")
            challenge = second.active_verification_challenge()

            self.assertIsNotNone(challenge)
            # O tempo restante continua contando do timestamp original — não
            # reiniciou por causa do restart.
            self.assertLessEqual(challenge.seconds_until_expiry(), remaining_before)
            self.assertGreater(challenge.seconds_until_expiry(), 0)
            self.assertFalse(user.email_verified)
            self.assertIsNone(second.verify_email_code(code))
        finally:
            await second.shutdown()

    # 12. rate limit de tentativas -----------------------------------------
    async def test_too_many_wrong_attempts_invalidates_challenge(self) -> None:
        account = self._account()
        try:
            await self._registered(account)
            await account.request_email_verification(force=True)
            correct = self.mailer.last_code
            wrong = "000000" if correct != "000000" else "111111"

            last_error = None
            for _ in range(MAX_ATTEMPTS):
                last_error = account.verify_email_code(wrong)

            self.assertEqual(last_error.code, AppErrorCode.VERIFICATION_TOO_MANY_ATTEMPTS)
            # Mesmo o código CORRETO não vale mais — o desafio foi queimado.
            after = account.verify_email_code(correct)
            self.assertIsNotNone(after)
            self.assertFalse(account.current_user.email_verified)
        finally:
            await account.shutdown()

    # --- e-mail não configurado -------------------------------------------
    async def test_unconfigured_email_service_never_pretends_to_send(self) -> None:
        mailer = FakeEmailService(configured=False)
        account = self._account(mailer=mailer)
        try:
            await self._registered(account)
            result = await account.request_email_verification(force=True)

            self.assertFalse(result.sent)
            self.assertEqual(result.error.code, AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED)
            self.assertEqual(mailer.sent, [])
            self.assertIsNone(account.active_verification_challenge())
        finally:
            await account.shutdown()

    async def test_send_failure_does_not_leave_an_orphan_challenge(self) -> None:
        mailer = FakeEmailService(fail=True)
        account = self._account(mailer=mailer)
        try:
            await self._registered(account)
            result = await account.request_email_verification(force=True)

            self.assertFalse(result.sent)
            # Nenhum desafio ativo: o usuário nunca recebeu o código, então
            # deixar um pendente só bloquearia o reenvio por 60s à toa.
            self.assertIsNone(account.active_verification_challenge())
        finally:
            await account.shutdown()

    # --- contas legacy (v0.9, sem e-mail) ---------------------------------
    async def test_legacy_account_without_email_keeps_working(self) -> None:
        account = self._account()
        try:
            user = await account.register(
                username="legacy", display_name="Legacy", password="senha-forte-123"
            )
            self.assertIsNone(user.email)
            self.assertFalse(user.email_verified)

            # Pedir verificação sem e-mail falha de forma clara, mas a conta
            # continua utilizável (login, chats, memória).
            result = await account.request_email_verification(force=True)
            self.assertFalse(result.sent)

            updated = account.set_email("legacy@example.com")
            self.assertEqual(updated.email, "legacy@example.com")

            sent = await account.request_email_verification(force=True)
            self.assertTrue(sent.sent)
            self.assertIsNone(account.verify_email_code(self.mailer.last_code))
        finally:
            await account.shutdown()


class VerificationHelpersTests(unittest.TestCase):
    def test_generate_code_is_six_digits(self) -> None:
        for _ in range(200):
            code = generate_code()
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())

    def test_mask_email_hides_most_of_the_local_part(self) -> None:
        self.assertEqual(mask_email("davi@example.com"), "d***@example.com")
        self.assertEqual(mask_email("a@b.com"), "a***@b.com")
        self.assertEqual(mask_email("semarroba"), "***")

    def test_cooldown_constant_matches_the_spec(self) -> None:
        self.assertEqual(RESEND_COOLDOWN_SECONDS, 60)


if __name__ == "__main__":
    unittest.main()
