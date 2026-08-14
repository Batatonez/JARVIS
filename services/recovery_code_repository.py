"""Códigos de recuperação de 2FA (v1.3, itens 49-50).

Servem para entrar quando o autenticador foi perdido — ou seja, são
credenciais de força total. Tratamento equivalente ao de senha:

- **Só o hash vai para o banco** (`services/password_hashing.py`, scrypt).
  Nem o JARVIS consegue recuperá-los depois — o plaintext existe apenas no
  retorno de `generate()`, mostrado UMA vez ao usuário.
- **Uso único**: consumir marca `used_at`; o mesmo código nunca serve duas
  vezes, nem em duas janelas de tempo diferentes.
- **Nunca em log**: nenhuma função aqui loga o código, nem em DEBUG.

Formato: `XXXX-XXXX`, com alfabeto sem caracteres ambíguos (sem O/0, I/1/L),
porque este é um código que o usuário anota no papel e digita à mão.
"""

import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from services.password_hashing import hash_password, verify_password

logger = logging.getLogger(__name__)

CODE_COUNT = 10
_GROUP_SIZE = 4
_GROUPS = 2
# Sem 0/O, 1/I/L — alfabeto de 30 símbolos. 8 símbolos ~ 39 bits por código:
# muito além do que se quebra por tentativa manual, e o backoff do 2FA
# (ver UserRepository.register_totp_failure) cobre o resto.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_code() -> str:
    groups = [
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_SIZE)) for _ in range(_GROUPS)
    ]
    return "-".join(groups)


def normalize_code(code: str) -> str:
    """Aceita o código como o usuário digitaria (minúsculo, sem hífen, com
    espaços) e devolve a forma canônica usada no hash."""
    cleaned = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if len(cleaned) != _GROUP_SIZE * _GROUPS:
        return cleaned
    return "-".join(
        cleaned[index : index + _GROUP_SIZE] for index in range(0, len(cleaned), _GROUP_SIZE)
    )


class RecoveryCodeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def generate(self, user_id: str, *, count: int = CODE_COUNT) -> list[str]:
        """Substitui TODOS os códigos da conta por um conjunto novo e devolve
        o plaintext — a única vez que ele existe (itens 49 e 50: regenerar
        invalida os antigos)."""
        codes = [_generate_code() for _ in range(count)]
        now = datetime.now(timezone.utc).isoformat()
        rows = [(str(uuid4()), user_id, hash_password(code), now) for code in codes]

        self._conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
        self._conn.executemany(
            "INSERT INTO recovery_codes (id, user_id, code_hash, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        logger.info("Códigos de recuperação regenerados (%s códigos).", len(codes))
        return codes

    def consume(self, user_id: str, code: str) -> bool:
        """Valida e queima um código. `False` se não bater ou se já tiver sido
        usado. Sempre escopado por `user_id`: o código de uma conta nunca
        pode abrir outra."""
        candidate = normalize_code(code)
        if not candidate:
            return False
        rows = self._conn.execute(
            "SELECT id, code_hash FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        ).fetchall()
        for row in rows:
            if verify_password(candidate, row["code_hash"]):
                cursor = self._conn.execute(
                    "UPDATE recovery_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
                self._conn.commit()
                # `rowcount == 0` significa que outra requisição consumiu o
                # mesmo código entre o SELECT e o UPDATE — o UPDATE
                # condicional é o que garante o uso único sob corrida.
                if cursor.rowcount > 0:
                    logger.info("Código de recuperação consumido.")
                    return True
                return False
        return False

    def remaining(self, user_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        ).fetchone()
        return int(row["total"]) if row else 0

    def clear(self, user_id: str) -> None:
        """Remove todos os códigos — usado ao desativar o 2FA (item 51)."""
        self._conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
        self._conn.commit()
