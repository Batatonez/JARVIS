"""Repositório de contas locais — único módulo que lê/escreve a tabela
`users` e o único lugar que toca `password_hash`. `app.models.User` (o tipo
que sai daqui para o resto do sistema) nunca carrega o hash.

**v1.0**: e-mail (obrigatório para contas novas, opcional para contas
legacy da v0.9), flag de verificação, e proteção contra força bruta no
login (backoff temporário crescente, nunca bloqueio permanente).
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Plan, User
from services.password_hashing import hash_password, verify_password

logger = logging.getLogger(__name__)

# Backoff de login: a partir da 5ª falha consecutiva, cada nova falha
# bloqueia por um tempo que dobra (30s, 60s, 120s...), com teto de 15min.
# Nunca é permanente — um bloqueio permanente transformaria a proteção em
# uma negação de serviço contra o próprio dono da conta.
_FAILURES_BEFORE_LOCKOUT = 5
_BASE_LOCKOUT_SECONDS = 30
_MAX_LOCKOUT_SECONDS = 15 * 60


class UsernameAlreadyExistsError(Exception):
    """Levantada por `create_user()` quando o username (normalizado) já existe."""


class EmailAlreadyRegisteredError(Exception):
    """Levantada por `create_user()` quando o e-mail já pertence a outra conta.

    Mensagem deliberadamente genérica: em um fluxo de cadastro, confirmar
    "este e-mail existe" enumera contas alheias. O username é diferente —
    é um identificador escolhido publicamente e precisa de erro específico
    para o cadastro ser usável."""


class InvalidCredentialsError(Exception):
    """Username ou senha inválidos — deliberadamente a mesma mensagem para os
    dois casos (não revelar se um username existe via timing/mensagem)."""


class AccountLockedError(Exception):
    """Tentativas demais em sequência; a conta está em cooldown temporário."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Muitas tentativas de login. Tente novamente em {retry_after_seconds} segundos."
        )


def _normalize_username(username: str) -> str:
    return username.strip().lower()


def normalize_email(email: str) -> str:
    """Normalização conservadora: remove espaços e caixa. Não mexe em
    sub-endereçamento (`+tag`) nem em pontos do Gmail — regras de provider
    específico não pertencem a uma normalização genérica, e removê-los
    impediria endereços legítimos de coexistirem."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def create_user(
        self, *, username: str, display_name: str, password: str, email: str | None = None
    ) -> User:
        normalized = _normalize_username(username)
        if not normalized:
            raise ValueError("Username não pode ser vazio.")
        display_name = display_name.strip() or normalized
        if not password:
            raise ValueError("Senha não pode ser vazia.")

        normalized_email = normalize_email(email) if email else None
        if normalized_email is not None and self.find_by_email(normalized_email) is not None:
            raise EmailAlreadyRegisteredError("Não foi possível criar a conta com esses dados.")

        password_hash = hash_password(password)
        user_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        try:
            self._conn.execute(
                "INSERT INTO users (id, username, display_name, password_hash, plan, created_at, email, email_verified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (user_id, normalized, display_name, password_hash, Plan.FREE.value, created_at, normalized_email),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            # O índice único de e-mail também cai aqui em caso de corrida
            # entre a checagem acima e o INSERT.
            if "email" in str(exc).lower():
                raise EmailAlreadyRegisteredError("Não foi possível criar a conta com esses dados.") from exc
            raise UsernameAlreadyExistsError(f"O username '{normalized}' já existe.") from exc

        return User(
            id=user_id,
            username=normalized,
            display_name=display_name,
            plan=Plan.FREE,
            email=normalized_email,
            email_verified=False,
        )

    def authenticate(self, *, username: str, password: str) -> User:
        """Valida credenciais. Levanta `AccountLockedError` se a conta estiver
        em cooldown por tentativas repetidas, e `InvalidCredentialsError` para
        usuário inexistente OU senha errada (mesma exceção, mesma mensagem)."""
        normalized = _normalize_username(username)
        row = self._conn.execute(
            "SELECT id, username, display_name, password_hash, plan, email, email_verified, "
            "       failed_login_attempts, lockout_until "
            "FROM users WHERE username = ?",
            (normalized,),
        ).fetchone()

        if row is None:
            # Ainda executa um hash descartável para não vazar por timing que
            # o usuário não existe (a diferença entre "sem usuário" e "senha
            # errada" seria de centenas de ms sem isto).
            verify_password(password, "scrypt$32768$8$1$00$00")
            raise InvalidCredentialsError("Username ou senha incorretos.")

        remaining = self._lockout_remaining_seconds(row["lockout_until"])
        if remaining > 0:
            raise AccountLockedError(remaining)

        if not verify_password(password, row["password_hash"]):
            self._register_failed_attempt(row["id"], row["failed_login_attempts"])
            raise InvalidCredentialsError("Username ou senha incorretos.")

        self._reset_failed_attempts(row["id"])
        return self._row_to_user(row)

    def get_user(self, user_id: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, username, display_name, plan, email, email_verified FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def find_by_email(self, email: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, username, display_name, plan, email, email_verified FROM users WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def has_any_user(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    def set_email(self, user_id: str, email: str) -> User | None:
        """Define/troca o e-mail de uma conta (usado por contas legacy da
        v0.9, que foram criadas sem e-mail). Sempre marca como NÃO
        verificado — trocar o e-mail exige verificar o novo."""
        normalized = normalize_email(email)
        existing = self.find_by_email(normalized)
        if existing is not None and existing.id != user_id:
            raise EmailAlreadyRegisteredError("Não foi possível usar esse e-mail.")
        self._conn.execute(
            "UPDATE users SET email = ?, email_verified = 0 WHERE id = ?", (normalized, user_id)
        )
        self._conn.commit()
        return self.get_user(user_id)

    def mark_email_verified(self, user_id: str) -> None:
        self._conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Proteção contra força bruta
    # ------------------------------------------------------------------

    @staticmethod
    def _lockout_remaining_seconds(lockout_until: str | None) -> int:
        if not lockout_until:
            return 0
        try:
            until = datetime.fromisoformat(lockout_until)
        except ValueError:
            return 0
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining + 0.999))  # arredonda para cima: nunca reporta "0s" ainda bloqueado

    def _register_failed_attempt(self, user_id: str, current_failures: int) -> None:
        failures = (current_failures or 0) + 1
        lockout_until: str | None = None
        if failures >= _FAILURES_BEFORE_LOCKOUT:
            steps = failures - _FAILURES_BEFORE_LOCKOUT
            seconds = min(_BASE_LOCKOUT_SECONDS * (2**steps), _MAX_LOCKOUT_SECONDS)
            lockout_until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
            logger.warning("Conta em cooldown por %ss após %s tentativas falhas.", seconds, failures)
        self._conn.execute(
            "UPDATE users SET failed_login_attempts = ?, lockout_until = ? WHERE id = ?",
            (failures, lockout_until, user_id),
        )
        self._conn.commit()

    def _reset_failed_attempts(self, user_id: str) -> None:
        self._conn.execute(
            "UPDATE users SET failed_login_attempts = 0, lockout_until = NULL WHERE id = ?", (user_id,)
        )
        self._conn.commit()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        keys = row.keys()
        return User(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            plan=Plan(row["plan"]),
            email=row["email"] if "email" in keys else None,
            email_verified=bool(row["email_verified"]) if "email_verified" in keys else False,
        )
