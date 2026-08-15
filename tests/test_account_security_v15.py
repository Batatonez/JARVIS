"""Segurança de conta e controles de provider (v1.5.0).

Nenhum teste aqui abre o `.env` real, o banco real, envia e-mail, chama
provider de IA, gasta dinheiro, toca microfone ou dorme esperando tempo
passar: onde há tempo envolvido (rate limit), o relógio é injetado.
"""

import unittest

from app.models import AppErrorCode
from services import password_policy
from services.account_service import AccountService
from services.local_database import SCHEMA_VERSION, connect
from services.login_throttle import (
    AuthChannel,
    LoginThrottle,
    LoginThrottled,
    normalize_identifier,
)
from services.password_policy import PasswordStrength
from services.reauth import ReauthGuard, SensitiveAction
from services.security_event_repository import (
    MAX_EVENTS_PER_USER,
    SecurityEventRepository,
    SecurityEventType,
    sanitize_metadata,
)
from services.session_repository import SessionRepository
from services.user_repository import UserRepository

_GOOD_PASSWORD = "chave-longa-987"


class _FakeClock:
    """Relógio controlado — nenhum teste de rate limit dorme de verdade."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _memory_db():
    return connect(":memory:")


# ======================================================================
# ARMAZENAMENTO DE SESSÃO (#1-6)
# ======================================================================


class SessionStorageTests(unittest.TestCase):
    """O pedido da v1.5.0 fala em "token de sessão em LocalStorage". Este app
    é desktop (PySide6/QML) e nunca teve LocalStorage, cookie ou
    `sessionStorage` — a auditoria confirmou isso e estes testes fixam o
    resultado, para que a propriedade não se perca numa versão futura."""

    def setUp(self) -> None:
        self.conn = _memory_db()
        self.users = UserRepository(self.conn)
        self.sessions = SessionRepository(self.conn)
        self.user = self.users.create_user(
            username="davi", display_name="Davi", password=_GOOD_PASSWORD, email="d@example.com"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_token_is_never_stored_in_clear_text_in_the_database(self) -> None:
        token = self.sessions.create_session(self.user.id)
        rows = self.conn.execute("SELECT token_hash FROM sessions").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["token_hash"], token)
        self.assertNotIn(token, rows[0]["token_hash"])

    def test_token_hash_is_sha256_hex(self) -> None:
        import hashlib

        token = self.sessions.create_session(self.user.id)
        row = self.conn.execute("SELECT token_hash FROM sessions").fetchone()
        self.assertEqual(row["token_hash"], hashlib.sha256(token.encode("utf-8")).hexdigest())

    def test_session_id_exposed_to_the_ui_cannot_reconstruct_the_token(self) -> None:
        """O identificador que a tela mostra é um prefixo do HASH — ele não
        autentica nada e não permite voltar ao token."""
        token = self.sessions.create_session(self.user.id)
        [info] = self.sessions.list_sessions(self.user.id, current_token=token)
        self.assertNotIn(info.session_id, token)
        self.assertIsNone(self.sessions.validate_session(info.session_id))

    def test_session_info_carries_no_token_field(self) -> None:
        token = self.sessions.create_session(self.user.id)
        [info] = self.sessions.list_sessions(self.user.id, current_token=token)
        for value in vars(info).values():
            self.assertNotEqual(value, token)

    def test_no_frontend_file_persists_a_session_token(self) -> None:
        """Varredura estática: nenhum arquivo de frontend pode guardar
        credencial por conta própria."""
        from pathlib import Path

        frontend = Path(__file__).resolve().parent.parent / "frontend"
        forbidden = ("localStorage", "sessionStorage", "document.cookie", "Qt.labs.settings")
        for path in list(frontend.rglob("*.qml")) + list(frontend.rglob("*.py")):
            content = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, content, f"{path.name} usa {needle}")

    def test_expired_session_is_rejected_and_removed(self) -> None:
        token = self.sessions.create_session(self.user.id)
        self.conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
        self.conn.commit()
        self.assertIsNone(self.sessions.validate_session(token))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)


# ======================================================================
# AUTORIZAÇÃO E OWNERSHIP (#7-12)
# ======================================================================


class AuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _memory_db()
        self.users = UserRepository(self.conn)
        self.sessions = SessionRepository(self.conn)
        self.events = SecurityEventRepository(self.conn)
        self.reauth = ReauthGuard()
        self.service = AccountService(
            self.users, self.sessions, reauth=self.reauth, events=self.events
        )
        self.alice = self.users.create_user(
            username="alice", display_name="Alice", password=_GOOD_PASSWORD, email="a@example.com"
        )
        self.bob = self.users.create_user(
            username="bob", display_name="Bob", password="outra-chave-321", email="b@example.com"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_no_client_side_role_check_exists_in_qml(self) -> None:
        """Varredura estática: nenhuma decisão de autorização pode depender de
        estado do frontend. `isAdmin`, `role`, `isOwner` em QML seriam
        exatamente isso."""
        from pathlib import Path

        qml_dir = Path(__file__).resolve().parent.parent / "frontend" / "qml"
        for path in qml_dir.rglob("*.qml"):
            content = path.read_text(encoding="utf-8")
            for needle in ("isAdmin", "isSuperuser", "hasRole", "userRole"):
                self.assertNotIn(needle, content, f"{path.name} decide autorização no cliente")

    def test_revoking_a_session_of_another_user_does_nothing(self) -> None:
        bob_token = self.sessions.create_session(self.bob.id)
        [bob_session] = self.sessions.list_sessions(self.bob.id, current_token=bob_token)

        self.reauth.confirm()
        _was_current, error = self.service.revoke_session(
            user_id=self.alice.id, session_id=bob_session.session_id, current_token=None
        )
        self.assertIsNotNone(error)
        # A sessão do Bob continua viva.
        self.assertEqual(self.sessions.validate_session(bob_token), self.bob.id)

    def test_log_out_others_never_touches_another_account(self) -> None:
        alice_token = self.sessions.create_session(self.alice.id)
        bob_token = self.sessions.create_session(self.bob.id)
        self.reauth.confirm()
        self.service.log_out_other_sessions(user_id=self.alice.id, current_token=alice_token)
        self.assertEqual(self.sessions.validate_session(bob_token), self.bob.id)

    def test_security_events_are_scoped_to_the_owner(self) -> None:
        self.events.record(user_id=self.alice.id, event_type=SecurityEventType.PASSWORD_CHANGED)
        self.events.record(user_id=self.bob.id, event_type=SecurityEventType.LOGIN_SUCCEEDED)
        alice_events = self.service.list_security_events(user_id=self.alice.id)
        self.assertEqual(len(alice_events), 1)
        self.assertIs(alice_events[0].event_type, SecurityEventType.PASSWORD_CHANGED)

    def test_sensitive_session_operations_require_recent_reauthentication(self) -> None:
        token = self.sessions.create_session(self.alice.id)
        [session] = self.sessions.list_sessions(self.alice.id, current_token=token)
        _was_current, error = self.service.revoke_session(
            user_id=self.alice.id, session_id=session.session_id, current_token=token
        )
        self.assertIsNotNone(error)
        self.assertIs(error.code, AppErrorCode.REAUTH_REQUIRED)

    def test_every_sensitive_action_is_declared_in_the_enum(self) -> None:
        """Nenhuma ação sensível nova pode nascer fora do mecanismo central."""
        for name in ("CHANGE_PASSWORD", "CHANGE_EMAIL", "REVOKE_SESSIONS", "DELETE_ACCOUNT"):
            self.assertTrue(hasattr(SensitiveAction, name))


# ======================================================================
# DISPONIBILIDADE DE E-MAIL E USERNAME (#13-23)
# ======================================================================


class IdentityAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _memory_db()
        self.users = UserRepository(self.conn)
        self.service = AccountService(
            self.users, SessionRepository(self.conn), reauth=ReauthGuard()
        )
        self.users.create_user(
            username="davi", display_name="Davi", password=_GOOD_PASSWORD, email="davi@example.com"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_free_email_is_reported_available(self) -> None:
        available, _message = self.service.check_email_available("novo@example.com")
        self.assertTrue(available)

    def test_taken_email_is_reported_unavailable(self) -> None:
        available, message = self.service.check_email_available("davi@example.com")
        self.assertFalse(available)
        self.assertIn("uso", message.lower())

    def test_email_availability_is_case_insensitive(self) -> None:
        available, _message = self.service.check_email_available("DAVI@EXAMPLE.COM")
        self.assertFalse(available)

    def test_malformed_email_is_reported_invalid_not_available(self) -> None:
        available, message = self.service.check_email_available("nao-e-email")
        self.assertFalse(available)
        self.assertIn("válido", message.lower())

    def test_availability_check_does_not_create_anything(self) -> None:
        self.service.check_email_available("fantasma@example.com")
        self.service.check_username_available("fantasma")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_database_unique_index_is_the_final_authority(self) -> None:
        """A checagem antecipada é usabilidade; quem fecha a corrida é o
        índice UNIQUE. Simulamos a corrida ignorando o resultado da consulta e
        criando mesmo assim."""
        from services.user_repository import EmailAlreadyRegisteredError

        self.service.check_email_available("davi@example.com")  # diz "em uso"
        with self.assertRaises(EmailAlreadyRegisteredError):
            self.users.create_user(
                username="outro", display_name="Outro", password="mais-uma-chave-1",
                email="DAVI@example.com",
            )

    def test_email_uniqueness_survives_whitespace(self) -> None:
        available, _message = self.service.check_email_available("  davi@example.com  ")
        self.assertFalse(available)

    def test_free_username_is_reported_available(self) -> None:
        available, _message = self.service.check_username_available("novo.usuario")
        self.assertTrue(available)

    def test_taken_username_is_reported_unavailable_with_a_specific_message(self) -> None:
        """Username PODE ser específico — ele é escolhido publicamente e o
        cadastro fica impossível sem esse retorno. É diferente do login, que
        nunca distingue conta inexistente de senha errada."""
        available, message = self.service.check_username_available("DAVI")
        self.assertFalse(available)
        self.assertIn("username", message.lower())

    def test_invalid_username_is_rejected_with_the_format_rule(self) -> None:
        available, message = self.service.check_username_available("a b!")
        self.assertFalse(available)
        self.assertTrue(message)


# ======================================================================
# RATE LIMIT DE LOGIN (#24-33)
# ======================================================================


class LoginRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.throttle = LoginThrottle(time_source=self.clock)

    def _fail(self, times: int, identifier: str = "davi") -> int:
        seconds = 0
        for _ in range(times):
            seconds = self.throttle.register_failure(identifier)
        return seconds

    def test_first_attempts_are_free(self) -> None:
        self.assertEqual(self._fail(5), 0)
        self.assertEqual(self.throttle.retry_after("davi"), 0)

    def test_cooldown_starts_after_the_free_attempts(self) -> None:
        self.assertGreater(self._fail(6), 0)
        self.assertGreater(self.throttle.retry_after("davi"), 0)

    def test_check_raises_while_blocked(self) -> None:
        self._fail(6)
        with self.assertRaises(LoginThrottled):
            self.throttle.check("davi")

    def test_backoff_is_progressive(self) -> None:
        first = self._fail(6)
        second = self.throttle.register_failure("davi")
        self.assertGreater(second, first)

    def test_cooldown_has_a_ceiling_and_is_never_permanent(self) -> None:
        seconds = self._fail(40)
        self.assertLessEqual(seconds, 15 * 60)
        self.clock.advance(seconds + 1)
        self.assertEqual(self.throttle.retry_after("davi"), 0)

    def test_cooldown_expires_with_the_injected_clock_without_sleeping(self) -> None:
        seconds = self._fail(6)
        self.clock.advance(seconds - 1)
        self.assertGreater(self.throttle.retry_after("davi"), 0)
        self.clock.advance(2)
        self.throttle.check("davi")  # não levanta

    def test_success_clears_the_state(self) -> None:
        self._fail(6)
        self.throttle.register_success("davi")
        self.assertEqual(self.throttle.retry_after("davi"), 0)

    def test_identifier_is_normalized_so_case_cannot_multiply_the_budget(self) -> None:
        self._fail(6, identifier="Davi")
        self.assertGreater(self.throttle.retry_after("  DAVI  "), 0)
        self.assertEqual(normalize_identifier("  DAVI  "), "davi")

    def test_channels_have_independent_budgets(self) -> None:
        """Errar o código do autenticador não pode consumir tentativas de
        senha — são fatores diferentes."""
        for _ in range(6):
            self.throttle.register_failure("davi", AuthChannel.TWO_FACTOR)
        self.assertGreater(self.throttle.retry_after("davi", AuthChannel.TWO_FACTOR), 0)
        self.assertEqual(self.throttle.retry_after("davi", AuthChannel.PASSWORD), 0)

    def test_nonexistent_identifier_is_throttled_too(self) -> None:
        """Era a lacuna que este módulo fecha: o backoff por conta do
        `UserRepository` nunca contava tentativas contra um identificador que
        não existe, porque não havia linha para incrementar."""
        self._fail(6, identifier="conta-que-nao-existe")
        self.assertGreater(self.throttle.retry_after("conta-que-nao-existe"), 0)

    def test_recovery_channel_is_also_protected(self) -> None:
        for _ in range(6):
            self.throttle.register_failure("davi", AuthChannel.RECOVERY_CODE)
        with self.assertRaises(LoginThrottled):
            self.throttle.check("davi", AuthChannel.RECOVERY_CODE)


class AccountEnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _memory_db()
        self.users = UserRepository(self.conn)
        self.users.create_user(
            username="davi", display_name="Davi", password=_GOOD_PASSWORD, email="d@example.com"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_wrong_password_and_missing_account_give_the_same_message(self) -> None:
        from services.user_repository import InvalidCredentialsError

        with self.assertRaises(InvalidCredentialsError) as wrong:
            self.users.authenticate(identifier="davi", password="errada-mas-longa")
        with self.assertRaises(InvalidCredentialsError) as missing:
            self.users.authenticate(identifier="ninguem", password="errada-mas-longa")
        self.assertEqual(str(wrong.exception), str(missing.exception))


# ======================================================================
# POLÍTICA DE SENHA (#34-43)
# ======================================================================


class PasswordPolicyTests(unittest.TestCase):
    def test_minimum_length_is_twelve(self) -> None:
        self.assertEqual(password_policy.MIN_LENGTH, 12)
        with self.assertRaises(password_policy.InvalidPasswordError):
            password_policy.validate_password("curta12345")

    def test_long_passphrase_with_spaces_is_accepted(self) -> None:
        phrase = "meu cachorro dorme no sofa azul"
        self.assertEqual(password_policy.validate_password(phrase), phrase)

    def test_unicode_password_is_accepted_and_never_truncated(self) -> None:
        senha = "coração-ração-日本語"
        self.assertEqual(password_policy.validate_password(senha), senha)

    def test_password_is_returned_unchanged(self) -> None:
        """Normalizar ou cortar espaço mudaria silenciosamente o que o usuário
        escolheu — ele nunca mais entraria digitando o que digitou."""
        senha = "  espaco nas pontas  "
        self.assertEqual(password_policy.validate_password(senha), senha)

    def test_common_password_is_rejected(self) -> None:
        with self.assertRaises(password_policy.InvalidPasswordError):
            password_policy.validate_password("password123")

    def test_common_password_check_is_case_insensitive(self) -> None:
        self.assertTrue(password_policy.is_common_password("PassWord123"))

    def test_password_derived_from_username_is_rejected(self) -> None:
        with self.assertRaises(password_policy.InvalidPasswordError):
            password_policy.validate_password("batatonez-2024", username="batatonez")

    def test_password_derived_from_email_local_part_is_rejected(self) -> None:
        self.assertTrue(
            password_policy.derives_from_account("davicunzolo!!", email="davicunzolo@example.com")
        )

    def test_password_derived_from_display_name_word_is_rejected(self) -> None:
        self.assertTrue(
            password_policy.derives_from_account("cunzolo-forte-1", display_name="Davi Cunzolo")
        )

    def test_maximum_length_protects_the_hasher(self) -> None:
        with self.assertRaises(password_policy.InvalidPasswordError):
            password_policy.validate_password("a" * (password_policy.MAX_LENGTH + 1))

    def test_strength_labels(self) -> None:
        self.assertIs(password_policy.strength_of("curta"), PasswordStrength.WEAK)
        self.assertIs(password_policy.strength_of("password123456"), PasswordStrength.WEAK)
        self.assertIs(password_policy.strength_of("apenasminusculas"), PasswordStrength.STRONG)
        # 13 caracteres, uma única classe: passa do piso mas não chega a
        # FORTE. (Não usar sequências de alfabeto aqui — a checagem de senha
        # comum as pega por prefixo, e o teste mediria outra coisa.)
        self.assertIs(password_policy.strength_of("girassolverde"), PasswordStrength.MEDIUM)

    def test_assessment_never_returns_the_password(self) -> None:
        senha = "uma-senha-bem-longa-1"
        assessment = password_policy.assess(senha)
        self.assertNotIn(senha, repr(assessment))

    def test_ui_assessment_and_backend_validation_agree(self) -> None:
        """A UI usa `assess`, o backend usa `validate_password`. Divergir aqui
        produziria o clássico "a tela disse ok e o servidor recusou"."""
        for candidate in ("curta", "password123", "uma-frase-longa-aqui", "batatonez-2024"):
            acceptable = password_policy.assess(candidate, username="batatonez").acceptable
            try:
                password_policy.validate_password(candidate, username="batatonez")
                validated = True
            except password_policy.InvalidPasswordError:
                validated = False
            self.assertEqual(acceptable, validated, candidate)


class PasswordHashingAuditTests(unittest.TestCase):
    """Auditoria do hashing existente (v1.5.0 pede auditar, não trocar)."""

    def test_hash_is_scrypt_with_a_random_salt(self) -> None:
        from services.password_hashing import hash_password

        first = hash_password("uma-senha-longa-1")
        second = hash_password("uma-senha-longa-1")
        self.assertTrue(first.startswith("scrypt$"))
        self.assertNotEqual(first, second, "salt precisa ser aleatório por hash")

    def test_password_never_appears_inside_the_stored_hash(self) -> None:
        from services.password_hashing import hash_password

        senha = "senha-muito-distinta-9"
        self.assertNotIn(senha, hash_password(senha))

    def test_verification_uses_constant_time_comparison(self) -> None:
        import inspect

        from services import password_hashing

        self.assertIn("compare_digest", inspect.getsource(password_hashing.verify_password))

    def test_malformed_hash_never_raises(self) -> None:
        from services.password_hashing import verify_password

        self.assertFalse(verify_password("x", "lixo-que-nao-e-hash"))

    def test_existing_short_password_still_authenticates(self) -> None:
        """Compatibilidade: subir o piso não pode expulsar quem já tinha
        conta. Gravamos um hash de senha curta direto no banco (como uma conta
        pré-v1.5.0 teria) e conferimos que o login continua funcionando."""
        from services.password_hashing import hash_password

        conn = _memory_db()
        try:
            users = UserRepository(conn)
            user = users.create_user(
                username="antigo", display_name="Antigo", password=_GOOD_PASSWORD
            )
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password("curta12"), user.id),
            )
            conn.commit()
            self.assertEqual(users.authenticate(identifier="antigo", password="curta12").id, user.id)
        finally:
            conn.close()


# ======================================================================
# SESSÕES (#44-50)
# ======================================================================


class SessionManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _memory_db()
        self.users = UserRepository(self.conn)
        self.sessions = SessionRepository(self.conn)
        self.events = SecurityEventRepository(self.conn)
        self.reauth = ReauthGuard()
        self.reauth.confirm()
        self.service = AccountService(
            self.users, self.sessions, reauth=self.reauth, events=self.events
        )
        self.user = self.users.create_user(
            username="davi", display_name="Davi", password=_GOOD_PASSWORD
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_listing_shows_creation_and_last_use(self) -> None:
        token = self.sessions.create_session(self.user.id)
        [info] = self.sessions.list_sessions(self.user.id, current_token=token)
        self.assertIsNotNone(info.created_at)
        self.assertIsNotNone(info.last_used_at)
        self.assertTrue(info.is_current)

    def test_listing_marks_only_the_current_session(self) -> None:
        current = self.sessions.create_session(self.user.id)
        self.sessions.create_session(self.user.id)
        sessions = self.sessions.list_sessions(self.user.id, current_token=current)
        self.assertEqual(sum(1 for s in sessions if s.is_current), 1)

    def test_listing_never_invents_ip_or_geolocation(self) -> None:
        token = self.sessions.create_session(self.user.id)
        [info] = self.sessions.list_sessions(self.user.id, current_token=token)
        fields = set(vars(info))
        for invented in ("ip", "ip_address", "city", "country", "location", "fingerprint"):
            self.assertNotIn(invented, fields)

    def test_revoking_another_session_keeps_the_current_one(self) -> None:
        current = self.sessions.create_session(self.user.id)
        other = self.sessions.create_session(self.user.id)
        other_id = self.sessions.session_id_for_token(other)
        was_current, error = self.service.revoke_session(
            user_id=self.user.id, session_id=other_id, current_token=current
        )
        self.assertIsNone(error)
        self.assertFalse(was_current)
        self.assertIsNone(self.sessions.validate_session(other))
        self.assertEqual(self.sessions.validate_session(current), self.user.id)

    def test_revoking_the_current_session_reports_it(self) -> None:
        current = self.sessions.create_session(self.user.id)
        current_id = self.sessions.session_id_for_token(current)
        was_current, error = self.service.revoke_session(
            user_id=self.user.id, session_id=current_id, current_token=current
        )
        self.assertIsNone(error)
        self.assertTrue(was_current)
        self.assertIsNone(self.sessions.validate_session(current))

    def test_revoking_an_unknown_session_reports_a_generic_error(self) -> None:
        _was_current, error = self.service.revoke_session(
            user_id=self.user.id, session_id="0" * 16, current_token=None
        )
        self.assertIsNotNone(error)

    def test_password_change_still_revokes_the_other_sessions(self) -> None:
        current = self.sessions.create_session(self.user.id)
        other = self.sessions.create_session(self.user.id)
        revoked, error = self.service.change_password(
            user_id=self.user.id,
            current_password=_GOOD_PASSWORD,
            new_password="outra-chave-nova-7",
            confirm_password="outra-chave-nova-7",
            current_token=current,
        )
        self.assertIsNone(error)
        self.assertEqual(revoked, 1)
        self.assertIsNone(self.sessions.validate_session(other))
        self.assertEqual(self.sessions.validate_session(current), self.user.id)


# ======================================================================
# ATIVIDADE DE SEGURANÇA (#51-57)
# ======================================================================


class SecurityActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _memory_db()
        self.users = UserRepository(self.conn)
        self.events = SecurityEventRepository(self.conn)
        self.user = self.users.create_user(
            username="davi", display_name="Davi", password=_GOOD_PASSWORD
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_migration_created_the_table(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 5)
        self.assertEqual(int(self.conn.execute("PRAGMA user_version").fetchone()[0]), SCHEMA_VERSION)
        self.conn.execute("SELECT event_id, user_id, event_type, created_at, safe_metadata_json "
                          "FROM security_events LIMIT 1")

    def test_event_is_recorded_and_read_back(self) -> None:
        self.events.record(user_id=self.user.id, event_type=SecurityEventType.LOGIN_SUCCEEDED)
        [event] = self.events.list_events(self.user.id)
        self.assertIs(event.event_type, SecurityEventType.LOGIN_SUCCEEDED)
        self.assertTrue(event.label)

    def test_secret_metadata_is_never_stored(self) -> None:
        self.events.record(
            user_id=self.user.id,
            event_type=SecurityEventType.LOGIN_SUCCEEDED,
            metadata={
                "password": "senha-secreta",
                "token": "tok_abc",
                "api_key": "sk-123",
                "authorization": "Bearer x",
                "totp_secret": "JBSWY3DP",
                "recovery_code": "AAAA-BBBB",
                "prompt": "conteúdo da conversa",
                "platform": "Windows 11",
            },
        )
        row = self.conn.execute("SELECT safe_metadata_json FROM security_events").fetchone()
        stored = row["safe_metadata_json"] or ""
        for secret in ("senha-secreta", "tok_abc", "sk-123", "Bearer", "JBSWY3DP", "AAAA-BBBB",
                       "conteúdo da conversa"):
            self.assertNotIn(secret, stored)
        self.assertIn("Windows 11", stored)

    def test_sanitize_metadata_drops_keys_outside_the_allowlist(self) -> None:
        self.assertEqual(sanitize_metadata({"password": "x", "platform": "Windows"}),
                         {"platform": "Windows"})

    def test_metadata_from_a_tampered_row_is_sanitized_on_read(self) -> None:
        self.events.record(user_id=self.user.id, event_type=SecurityEventType.LOGIN_SUCCEEDED)
        self.conn.execute(
            'UPDATE security_events SET safe_metadata_json = ?', ('{"password": "vazou"}',)
        )
        self.conn.commit()
        [event] = self.events.list_events(self.user.id)
        self.assertEqual(event.metadata, {})

    def test_users_only_see_their_own_events(self) -> None:
        other = self.users.create_user(
            username="bob", display_name="Bob", password="mais-uma-chave-1"
        )
        self.events.record(user_id=other.id, event_type=SecurityEventType.PASSWORD_CHANGED)
        self.assertEqual(self.events.list_events(self.user.id), [])

    def test_retention_caps_the_number_of_events(self) -> None:
        for _ in range(MAX_EVENTS_PER_USER + 10):
            self.events.record(user_id=self.user.id, event_type=SecurityEventType.LOGIN_SUCCEEDED)
        self.assertLessEqual(self.events.count_events(self.user.id), MAX_EVENTS_PER_USER)

    def test_pagination_limits_the_page(self) -> None:
        for _ in range(10):
            self.events.record(user_id=self.user.id, event_type=SecurityEventType.LOGIN_SUCCEEDED)
        self.assertEqual(len(self.events.list_events(self.user.id, limit=3)), 3)
        self.assertEqual(len(self.events.list_events(self.user.id, limit=3, offset=9)), 1)

    def test_deleting_the_account_removes_its_events(self) -> None:
        self.events.record(user_id=self.user.id, event_type=SecurityEventType.LOGIN_SUCCEEDED)
        self.users.delete_user(self.user.id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM security_events").fetchone()[0], 0
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
