"""Repositório dos códigos de verificação de e-mail.

O código de 6 dígitos **nunca** é guardado em claro: o banco recebe só um
hash `scrypt` (reusando `services/password_hashing.py` — nenhuma
criptografia nova foi inventada aqui). Um código de 6 dígitos tem só 10^6
possibilidades, então a segurança real vem de três limites, não da força do
hash: expira em 5 minutos, é de uso único, e aceita no máximo
`MAX_ATTEMPTS` tentativas antes de ser invalidado. Isso está documentado
honestamente em docs/security.md.

Invariante central: **no máximo um código ativo por usuário**. Emitir um
novo invalida (consome) todos os anteriores, na mesma transação.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from uuid import uuid4

from services.password_hashing import hash_password, verify_password

# Regras fixadas para a v1.0 (itens 38/39/41 do escopo).
CODE_TTL_SECONDS = 5 * 60
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class VerificationChallenge:
    """Estado público de um desafio de verificação — nunca inclui o código
    nem o hash dele. É o que o HUD usa para os dois contadores da tela."""

    id: str
    user_id: str
    email: str
    expires_at: datetime
    resend_available_at: datetime
    attempts: int

    def seconds_until_expiry(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        return max(0, int((self.expires_at - now).total_seconds()))

    def seconds_until_resend(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        return max(0, int((self.resend_available_at - now).total_seconds()))


class EmailVerificationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def create_challenge(self, *, user_id: str, email: str, code: str) -> VerificationChallenge:
        """Invalida qualquer código anterior do usuário e cria um novo."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=CODE_TTL_SECONDS)
        resend_available_at = now + timedelta(seconds=RESEND_COOLDOWN_SECONDS)
        challenge_id = str(uuid4())

        # Consome os anteriores ANTES de inserir o novo — garante a
        # invariante "só um ativo por usuário" mesmo se algo falhar depois.
        self._conn.execute(
            "UPDATE email_verification_tokens SET consumed_at = ? WHERE user_id = ? AND consumed_at IS NULL",
            (now.isoformat(), user_id),
        )
        self._conn.execute(
            "INSERT INTO email_verification_tokens "
            "(id, user_id, email, code_hash, created_at, expires_at, resend_available_at, consumed_at, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0)",
            (
                challenge_id,
                user_id,
                email,
                hash_password(code),
                now.isoformat(),
                expires_at.isoformat(),
                resend_available_at.isoformat(),
            ),
        )
        self._conn.commit()
        return VerificationChallenge(
            id=challenge_id,
            user_id=user_id,
            email=email,
            expires_at=expires_at,
            resend_available_at=resend_available_at,
            attempts=0,
        )

    def active_challenge(self, user_id: str) -> VerificationChallenge | None:
        """O desafio ativo (não consumido) do usuário, expirado ou não —
        quem decide o que fazer com um expirado é o service."""
        row = self._conn.execute(
            "SELECT id, user_id, email, expires_at, resend_available_at, attempts "
            "FROM email_verification_tokens WHERE user_id = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return self._row_to_challenge(row) if row is not None else None

    def verify_code(self, *, user_id: str, code: str) -> tuple[bool, VerificationChallenge | None]:
        """Confere o código contra o desafio ativo. Devolve
        `(bateu, desafio_atualizado)`. Em acerto, o desafio é consumido
        (uso único). Em erro, incrementa tentativas — e consome o desafio ao
        estourar `MAX_ATTEMPTS`, para não permitir força bruta indefinida."""
        row = self._conn.execute(
            "SELECT id, user_id, email, code_hash, expires_at, resend_available_at, attempts "
            "FROM email_verification_tokens WHERE user_id = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            return (False, None)

        if verify_password(code, row["code_hash"]):
            self._consume(row["id"])
            return (True, self._row_to_challenge(row))

        attempts = int(row["attempts"]) + 1
        self._conn.execute(
            "UPDATE email_verification_tokens SET attempts = ? WHERE id = ?", (attempts, row["id"])
        )
        if attempts >= MAX_ATTEMPTS:
            self._consume(row["id"])
        self._conn.commit()

        updated = self._row_to_challenge(row)
        return (False, VerificationChallenge(
            id=updated.id,
            user_id=updated.user_id,
            email=updated.email,
            expires_at=updated.expires_at,
            resend_available_at=updated.resend_available_at,
            attempts=attempts,
        ))

    def consume_active(self, user_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE email_verification_tokens SET consumed_at = ? WHERE user_id = ? AND consumed_at IS NULL",
            (now, user_id),
        )
        self._conn.commit()

    def _consume(self, challenge_id: str) -> None:
        self._conn.execute(
            "UPDATE email_verification_tokens SET consumed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), challenge_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_challenge(row: sqlite3.Row) -> VerificationChallenge:
        return VerificationChallenge(
            id=row["id"],
            user_id=row["user_id"],
            email=row["email"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
            resend_available_at=datetime.fromisoformat(row["resend_available_at"]),
            attempts=int(row["attempts"]),
        )
