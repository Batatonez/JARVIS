"""2FA (TOTP), códigos de recuperação e reautenticação recente
(v1.3, itens 45-53, 68).

Tempo é MOCKADO em todo lugar (item 68): `totp.verify(..., at=...)` recebe o
instante, e o `ReauthGuard` recebe um relógio falso. Nenhum `sleep`.

Nenhum banco real, nenhum autenticador real, nenhuma rede.
"""

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from app.models import AppErrorCode
from services import totp
from services.local_database import connect
from services.reauth import ReauthGuard, SensitiveAction
from services.recovery_code_repository import RecoveryCodeRepository, normalize_code
from services.secret_protection import is_protected, protect, unprotect
from services.two_factor_service import TwoFactorService
from services.user_repository import UserRepository


class _FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ----------------------------------------------------------------------
# Algoritmo (RFC 6238/4226)
# ----------------------------------------------------------------------


class TotpAlgorithmTests(unittest.TestCase):
    # Vetor do RFC 4226 (Appendix D), chave ASCII "12345678901234567890".
    RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    RFC_HOTP = ["755224", "287082", "359152", "969429", "338314"]

    def test_matches_rfc4226_test_vectors(self) -> None:
        """Não inventamos algoritmo (item 46): os valores batem com o RFC."""
        for counter, expected in enumerate(self.RFC_HOTP):
            with self.subTest(counter=counter):
                self.assertEqual(totp.hotp(self.RFC_SECRET, counter), expected)

    def test_code_has_six_digits(self) -> None:
        secret = totp.generate_secret()
        code = totp.totp(secret, at=1_700_000_000)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_code_changes_every_period(self) -> None:
        secret = totp.generate_secret()
        self.assertNotEqual(totp.totp(secret, at=1000), totp.totp(secret, at=1000 + 30))

    def test_accepts_one_step_of_clock_drift(self) -> None:
        secret = totp.generate_secret()
        code = totp.totp(secret, at=1000)
        for offset in (-30, 0, 30):
            with self.subTest(offset=offset):
                self.assertTrue(totp.verify(secret, code, at=1000 + offset))

    def test_rejects_beyond_the_accepted_window(self) -> None:
        secret = totp.generate_secret()
        code = totp.totp(secret, at=1000)
        self.assertFalse(totp.verify(secret, code, at=1000 + 120))

    def test_rejects_malformed_codes(self) -> None:
        secret = totp.generate_secret()
        for bad in ("", "abc", "12345", "1234567", None):
            with self.subTest(bad=bad):
                self.assertFalse(totp.verify(secret, bad, at=1000))

    def test_secret_normalization_accepts_human_typing(self) -> None:
        secret = totp.generate_secret()
        typed = secret.lower()[:4] + " " + secret.lower()[4:]
        self.assertEqual(totp.normalize_secret(typed).rstrip("="), secret)

    def test_provisioning_uri_is_standard(self) -> None:
        uri = totp.provisioning_uri(secret="JBSWY3DPEHPK3PXP", account_name="davi@x.com", issuer="JARVIS")
        self.assertTrue(uri.startswith("otpauth://totp/JARVIS%3Adavi%40x.com?"))
        self.assertIn("secret=JBSWY3DPEHPK3PXP", uri)
        self.assertIn("algorithm=SHA1", uri)
        self.assertIn("digits=6", uri)
        self.assertIn("period=30", uri)

    def test_qr_matrix_is_square_and_boolean(self) -> None:
        matrix = totp.qr_matrix("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP")
        self.assertTrue(matrix)
        self.assertEqual(len(matrix), len(matrix[0]))
        self.assertIsInstance(matrix[0][0], bool)


# ----------------------------------------------------------------------
# Proteção do segredo (item 48)
# ----------------------------------------------------------------------


class SecretProtectionTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        self.assertEqual(unprotect(protect(secret)), secret)

    def test_stored_value_never_contains_the_plaintext(self) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        stored = protect(secret)
        self.assertNotIn(secret, stored)

    def test_format_is_self_describing(self) -> None:
        stored = protect("JBSWY3DPEHPK3PXP")
        self.assertTrue(stored.startswith(("dpapi:", "plain:")))

    def test_garbage_returns_none_without_raising(self) -> None:
        for bad in ("", "lixo", "dpapi:@@@", "desconhecido:AAAA"):
            with self.subTest(bad=bad):
                self.assertIsNone(unprotect(bad))

    def test_is_protected_only_true_for_real_encryption(self) -> None:
        self.assertFalse(is_protected("plain:AAAA"))
        self.assertFalse(is_protected(None))


# ----------------------------------------------------------------------
# Reautenticação recente (item 45)
# ----------------------------------------------------------------------


class ReauthGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.guard = ReauthGuard(window_seconds=300, time_source=self.clock)

    def test_starts_invalid(self) -> None:
        self.assertFalse(self.guard.is_valid())
        self.assertFalse(self.guard.require(SensitiveAction.DELETE_ACCOUNT))

    def test_valid_inside_the_window(self) -> None:
        self.guard.confirm()
        self.clock.advance(299)
        self.assertTrue(self.guard.require(SensitiveAction.CHANGE_EMAIL))

    def test_expires_after_the_window(self) -> None:
        self.guard.confirm()
        self.clock.advance(301)
        self.assertFalse(self.guard.require(SensitiveAction.CHANGE_EMAIL))

    def test_invalidate_closes_immediately(self) -> None:
        self.guard.confirm()
        self.guard.invalidate()
        self.assertFalse(self.guard.is_valid())

    def test_remaining_seconds_are_reported(self) -> None:
        self.guard.confirm()
        self.clock.advance(100)
        self.assertEqual(self.guard.state().remaining_seconds, 200)


# ----------------------------------------------------------------------
# Códigos de recuperação (itens 49-50)
# ----------------------------------------------------------------------


class _TempDatabase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.connection = connect(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.connection.close)
        self.users = UserRepository(self.connection)
        self.recovery = RecoveryCodeRepository(self.connection)
        self.user = self.users.create_user(
            username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
        )
        self.other = self.users.create_user(
            username="bob", display_name="Bob", password="senha-forte-123", email="b@example.com"
        )


class RecoveryCodeTests(_TempDatabase):
    def test_generates_ten_codes(self) -> None:
        self.assertEqual(len(self.recovery.generate(self.user.id)), 10)
        self.assertEqual(self.recovery.remaining(self.user.id), 10)

    def test_only_hashes_are_stored(self) -> None:
        codes = self.recovery.generate(self.user.id)
        stored = self.connection.execute(
            "SELECT code_hash FROM recovery_codes WHERE user_id = ?", (self.user.id,)
        ).fetchall()
        blob = " ".join(row["code_hash"] for row in stored)
        for code in codes:
            self.assertNotIn(code, blob)
        self.assertTrue(all(row["code_hash"].startswith("scrypt$") for row in stored))

    def test_code_is_single_use(self) -> None:
        code = self.recovery.generate(self.user.id)[0]
        self.assertTrue(self.recovery.consume(self.user.id, code))
        self.assertFalse(self.recovery.consume(self.user.id, code))
        self.assertEqual(self.recovery.remaining(self.user.id), 9)

    def test_accepts_human_formatting(self) -> None:
        code = self.recovery.generate(self.user.id)[0]
        self.assertTrue(self.recovery.consume(self.user.id, code.lower().replace("-", " ")))

    def test_code_of_one_account_never_opens_another(self) -> None:
        code = self.recovery.generate(self.user.id)[0]
        self.recovery.generate(self.other.id)
        self.assertFalse(self.recovery.consume(self.other.id, code))

    def test_regenerating_invalidates_the_old_set(self) -> None:
        old = self.recovery.generate(self.user.id)
        self.recovery.generate(self.user.id)
        self.assertFalse(self.recovery.consume(self.user.id, old[0]))
        self.assertEqual(self.recovery.remaining(self.user.id), 10)

    def test_alphabet_avoids_ambiguous_characters(self) -> None:
        for code in self.recovery.generate(self.user.id):
            self.assertNotRegex(code, r"[O0I1L]")

    def test_normalize_code_formats_groups(self) -> None:
        self.assertEqual(normalize_code("abcd efgh"), "ABCD-EFGH")


# ----------------------------------------------------------------------
# Serviço de 2FA (itens 47, 51, 52, 53)
# ----------------------------------------------------------------------


class TwoFactorServiceTests(_TempDatabase):
    def setUp(self) -> None:
        super().setUp()
        self.clock = _FakeClock()
        self.guard = ReauthGuard(window_seconds=300, time_source=self.clock)
        self.service = TwoFactorService(self.users, self.recovery, reauth=self.guard)

    def _enroll(self):
        self.guard.confirm()
        enrollment, error = self.service.start_enrollment(
            user_id=self.user.id, account_name="alice"
        )
        self.assertIsNone(error)
        return enrollment

    def _valid_code(self, enrollment):
        return totp.totp(enrollment.secret.replace(" ", ""))

    def test_enrollment_requires_recent_reauth(self) -> None:
        enrollment, error = self.service.start_enrollment(user_id=self.user.id, account_name="alice")
        self.assertIsNone(enrollment)
        self.assertEqual(error.code, AppErrorCode.REAUTH_REQUIRED)

    def test_two_factor_is_not_active_before_confirmation(self) -> None:
        """Item 47: só ativa depois do primeiro código correto."""
        self._enroll()
        self.assertFalse(self.users.is_totp_enabled(self.user.id))
        status = self.service.status(self.user.id)
        self.assertTrue(status.enrollment_pending)
        self.assertFalse(status.enabled)

    def test_wrong_code_does_not_activate(self) -> None:
        self._enroll()
        codes, error = self.service.confirm_enrollment(user_id=self.user.id, code="000000")
        self.assertIsNone(codes)
        self.assertEqual(error.code, AppErrorCode.TWO_FACTOR_INVALID)
        self.assertFalse(self.users.is_totp_enabled(self.user.id))

    def test_correct_code_activates_and_returns_recovery_codes(self) -> None:
        enrollment = self._enroll()
        codes, error = self.service.confirm_enrollment(
            user_id=self.user.id, code=self._valid_code(enrollment)
        )
        self.assertIsNone(error)
        self.assertEqual(len(codes), 10)
        self.assertTrue(self.users.is_totp_enabled(self.user.id))

    def test_secret_is_stored_protected_not_plaintext(self) -> None:
        enrollment = self._enroll()
        stored = self.users.get_totp_secret(self.user.id)
        self.assertNotIn(enrollment.secret.replace(" ", ""), stored)

    def test_secret_never_appears_in_logs(self) -> None:
        """Item 48/68: o segredo não pode vazar por log."""
        with self.assertLogs(level="DEBUG") as captured:
            enrollment = self._enroll()
            self.service.confirm_enrollment(
                user_id=self.user.id, code=self._valid_code(enrollment)
            )
        blob = "\n".join(captured.output)
        secret = enrollment.secret.replace(" ", "")
        self.assertNotIn(secret, blob)
        # A URI de provisionamento CONTÉM o segredo — também não pode vazar.
        self.assertNotIn("otpauth://", blob)

    def test_verification_accepts_totp(self) -> None:
        enrollment = self._enroll()
        self.service.confirm_enrollment(user_id=self.user.id, code=self._valid_code(enrollment))
        self.assertIsNone(self.service.verify(user_id=self.user.id, code=self._valid_code(enrollment)))

    def test_verification_accepts_recovery_code_once(self) -> None:
        enrollment = self._enroll()
        codes, _ = self.service.confirm_enrollment(
            user_id=self.user.id, code=self._valid_code(enrollment)
        )
        self.assertIsNone(self.service.verify(user_id=self.user.id, code=codes[0]))
        error = self.service.verify(user_id=self.user.id, code=codes[0])
        self.assertIsNotNone(error)

    def test_rate_limit_kicks_in_and_is_not_permanent(self) -> None:
        """Item 53: brute-force de 6 dígitos precisa de backoff, mas a conta
        nunca fica bloqueada para sempre."""
        enrollment = self._enroll()
        self.service.confirm_enrollment(user_id=self.user.id, code=self._valid_code(enrollment))
        for _ in range(5):
            self.service.verify(user_id=self.user.id, code="000000")
        error = self.service.verify(user_id=self.user.id, code="000000")
        self.assertEqual(error.code, AppErrorCode.TWO_FACTOR_RATE_LIMITED)
        self.assertGreater(self.users.totp_lockout_remaining(self.user.id), 0)
        # Tem prazo: some sozinho.
        self.users.reset_totp_failures(self.user.id)
        self.assertEqual(self.users.totp_lockout_remaining(self.user.id), 0)

    def test_disable_requires_reauth_and_second_factor(self) -> None:
        enrollment = self._enroll()
        self.service.confirm_enrollment(user_id=self.user.id, code=self._valid_code(enrollment))

        self.guard.invalidate()
        self.assertEqual(
            self.service.disable(user_id=self.user.id, code=self._valid_code(enrollment)).code,
            AppErrorCode.REAUTH_REQUIRED,
        )

        self.guard.confirm()
        self.assertEqual(
            self.service.disable(user_id=self.user.id, code="000000").code,
            AppErrorCode.TWO_FACTOR_INVALID,
        )
        self.assertTrue(self.users.is_totp_enabled(self.user.id))

    def test_disable_revokes_secret_and_recovery_codes(self) -> None:
        enrollment = self._enroll()
        self.service.confirm_enrollment(user_id=self.user.id, code=self._valid_code(enrollment))
        self.assertIsNone(self.service.disable(user_id=self.user.id, code=self._valid_code(enrollment)))

        self.assertFalse(self.users.is_totp_enabled(self.user.id))
        self.assertIsNone(self.users.get_totp_secret(self.user.id))
        self.assertEqual(self.recovery.remaining(self.user.id), 0)

    def test_regenerate_requires_totp_not_recovery_code(self) -> None:
        """Um código de recuperação vazado não pode se auto-renovar."""
        enrollment = self._enroll()
        codes, _ = self.service.confirm_enrollment(
            user_id=self.user.id, code=self._valid_code(enrollment)
        )
        new_codes, error = self.service.regenerate_recovery_codes(
            user_id=self.user.id, code=codes[0]
        )
        self.assertIsNone(new_codes)
        self.assertIsNotNone(error)

        new_codes, error = self.service.regenerate_recovery_codes(
            user_id=self.user.id, code=self._valid_code(enrollment)
        )
        self.assertIsNone(error)
        self.assertEqual(len(new_codes), 10)

    def test_verify_on_account_without_2fa_reports_not_enabled(self) -> None:
        error = self.service.verify(user_id=self.user.id, code="123456")
        self.assertEqual(error.code, AppErrorCode.TWO_FACTOR_NOT_ENABLED)


if __name__ == "__main__":
    unittest.main()
