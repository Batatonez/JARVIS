"""Repositório de contas locais — único módulo que lê/escreve a tabela
`users` e o único lugar que toca `password_hash`. `app.models.User` (o tipo
que sai daqui para o resto do sistema) nunca carrega o hash.
"""

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.models import Plan, User
from services.password_hashing import hash_password, verify_password


class UsernameAlreadyExistsError(Exception):
    """Levantada por `create_user()` quando o username (normalizado) já existe."""


class InvalidCredentialsError(Exception):
    """Username ou senha inválidos — deliberadamente a mesma mensagem para os
    dois casos (não revelar se um username existe via timing/mensagem)."""


def _normalize_username(username: str) -> str:
    return username.strip().lower()


class UserRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def create_user(self, *, username: str, display_name: str, password: str) -> User:
        normalized = _normalize_username(username)
        if not normalized:
            raise ValueError("Username não pode ser vazio.")
        display_name = display_name.strip() or normalized
        if not password:
            raise ValueError("Senha não pode ser vazia.")

        password_hash = hash_password(password)
        user_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        try:
            self._conn.execute(
                "INSERT INTO users (id, username, display_name, password_hash, plan, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, normalized, display_name, password_hash, Plan.FREE.value, created_at),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise UsernameAlreadyExistsError(f"O username '{normalized}' já existe.") from exc

        return User(id=user_id, username=normalized, display_name=display_name, plan=Plan.FREE)

    def authenticate(self, *, username: str, password: str) -> User:
        normalized = _normalize_username(username)
        row = self._conn.execute(
            "SELECT id, username, display_name, password_hash, plan FROM users WHERE username = ?",
            (normalized,),
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise InvalidCredentialsError("Username ou senha incorretos.")
        return self._row_to_user(row)

    def get_user(self, user_id: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, username, display_name, plan FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def has_any_user(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            plan=Plan(row["plan"]),
        )
