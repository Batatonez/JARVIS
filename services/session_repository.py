"""Repositório de sessões — token opaco (`secrets.token_urlsafe`), nunca a
senha, com expiração.

**v1.0**: o banco guarda apenas o **SHA-256 do token**, nunca o token em si
(mesmo princípio de um token de "lembrar-me": quem tem o banco não consegue
se passar por um usuário logado). O token em claro só existe em dois
lugares: na RAM da sessão atual e no arquivo local protegido por DPAPI
(`services/session_store.py`).

SHA-256 puro (e não scrypt como nas senhas) é adequado aqui porque o token
tem 256 bits de entropia vinda de `secrets` — não há espaço de busca a
proteger com custo artificial, ao contrário de uma senha escolhida por
humano. Ver docs/security.md.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

_TOKEN_BYTES = 32
_DEFAULT_TTL_DAYS = 30


def hash_token(token: str) -> str:
    """Hash determinístico do token, usado como chave primária de `sessions`.
    Determinístico de propósito: precisamos procurar por ele."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionRepository:
    def __init__(self, connection: sqlite3.Connection, *, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        self._conn = connection
        self._ttl = timedelta(days=ttl_days)

    def create_session(self, user_id: str) -> str:
        """Cria a sessão e devolve o token em claro — **única** vez que ele
        existe; o banco só recebe o hash."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = datetime.now(timezone.utc)
        expires_at = now + self._ttl
        self._conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (hash_token(token), user_id, now.isoformat(), expires_at.isoformat()),
        )
        self._conn.commit()
        return token

    def validate_session(self, token: str) -> str | None:
        """Retorna o `user_id` se o token existir e não estiver expirado —
        senão `None` (e, se estava expirado, apaga a sessão nesse momento)."""
        if not token:
            return None
        token_hash = hash_token(token)
        row = self._conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= datetime.now(timezone.utc):
            self._delete_by_hash(token_hash)
            return None
        return row["user_id"]

    def delete_session(self, token: str) -> None:
        self._delete_by_hash(hash_token(token))

    def delete_all_for_user(self, user_id: str) -> int:
        """Revoga TODAS as sessões de um usuário (ex.: troca de senha, ou um
        "sair de todos os dispositivos" futuro). Devolve quantas foram."""
        cursor = self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._conn.commit()
        return cursor.rowcount

    def _delete_by_hash(self, token_hash: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self._conn.commit()
