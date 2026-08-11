"""Repositório de sessões — token opaco (`secrets.token_urlsafe`), nunca a
senha, com expiração. `validate_session()` já remove sessões expiradas
(nunca deixa uma sessão vencida "meio válida")."""

import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

_TOKEN_BYTES = 32
_DEFAULT_TTL_DAYS = 30


class SessionRepository:
    def __init__(self, connection: sqlite3.Connection, *, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        self._conn = connection
        self._ttl = timedelta(days=ttl_days)

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = datetime.now(timezone.utc)
        expires_at = now + self._ttl
        self._conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires_at.isoformat()),
        )
        self._conn.commit()
        return token

    def validate_session(self, token: str) -> str | None:
        """Retorna o `user_id` se o token existir e não estiver expirado —
        senão `None` (e, se estava expirado, apaga a sessão nesse momento)."""
        if not token:
            return None
        row = self._conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= datetime.now(timezone.utc):
            self.delete_session(token)
            return None
        return row["user_id"]

    def delete_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self._conn.commit()
