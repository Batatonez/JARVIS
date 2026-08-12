"""Conexão SQLite local + schema versionado — `sqlite3` da biblioteca
padrão, sem ORM.

Única porta de entrada para o banco: `user_repository.py`,
`session_repository.py`, `conversation_repository.py` e
`email_verification_repository.py` chamam `connect()` e fazem suas próprias
queries, sempre parametrizadas (`?`) — nunca interpolação de string, para não
abrir espaço a SQL injection. O frontend (QML/Bridge) nunca importa este
módulo nem sabe que SQLite existe.

O banco vive em `settings.db_path` (`data/jarvis.db`), fora do Git (ver
`.gitignore`) — contém contas, sessões e conversas, tudo dado pessoal.

**Migrações (v1.0)**: o schema é versionado via `PRAGMA user_version` e
evolui por uma lista ordenada de migrações. Cada migração roda dentro de uma
transação própria: se falhar, o `ROLLBACK` devolve o banco ao estado
anterior e o erro sobe como `MigrationError` — nunca deixamos um banco
meio-migrado, e **nunca** apagamos/recriamos o banco para "resolver"
divergência de schema (ver docs/security.md, seção Persistência).
"""

import hashlib
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Versão de schema que este código espera. Incrementar SEMPRE que uma
# migração nova for adicionada a `_MIGRATIONS`.
SCHEMA_VERSION = 2


class MigrationError(Exception):
    """Falha ao migrar o banco. O banco foi preservado (rollback aplicado)."""


# --- v1 — schema original (v0.9) ------------------------------------------
# Mantido como uma migração de verdade (e não como um `CREATE TABLE IF NOT
# EXISTS` solto) para que um banco novo e um banco v0.9 existente cheguem
# exatamente ao mesmo estado final, pelo mesmo caminho.
_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
"""

# --- v2 — v1.0: e-mail verificado, proteção de brute-force, token de
# sessão guardado como hash ------------------------------------------------
_MIGRATION_2_DDL = """
ALTER TABLE users ADD COLUMN email TEXT;
ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN lockout_until TEXT;

-- Unicidade case-insensitive de e-mail. Índice parcial: contas legacy
-- (v0.9, sem e-mail) têm `email IS NULL` e não colidem entre si.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
    ON users(email) WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resend_available_at TEXT NOT NULL,
    consumed_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_email_tokens_user ON email_verification_tokens(user_id);

-- `sessions` passa a guardar SHA-256 do token, nunca o token em si (ver
-- services/session_repository.py). SQLite não permite renomear/mudar a PK
-- in-place, então recriamos a tabela e copiamos os dados — as sessões
-- existentes continuam válidas (o hash é calculado a partir do token
-- guardado, em `_migrate_session_tokens`).
CREATE TABLE IF NOT EXISTS sessions_v2 (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def _migrate_session_tokens(connection: sqlite3.Connection) -> None:
    """Copia `sessions` -> `sessions_v2` trocando o token em claro pelo seu
    SHA-256. Feito em Python porque o SQLite não tem SHA-256 embutido.
    Nenhuma sessão é invalidada: o token que o usuário já tem no disco
    continua resolvendo para o mesmo hash."""
    rows = connection.execute("SELECT token, user_id, created_at, expires_at FROM sessions").fetchall()
    for row in rows:
        token_hash = hashlib.sha256(row["token"].encode("utf-8")).hexdigest()
        connection.execute(
            "INSERT OR REPLACE INTO sessions_v2 (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, row["user_id"], row["created_at"], row["expires_at"]),
        )
    connection.execute("DROP TABLE sessions")
    connection.execute("ALTER TABLE sessions_v2 RENAME TO sessions")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")


def _statements(script: str) -> list[str]:
    """Divide um script DDL em statements. Deliberadamente ingênuo (split em
    `;`) — só é seguro porque estes scripts não têm literal de string nem
    trigger com `;` embutido. NÃO usamos `executescript()`: ele emite um
    COMMIT implícito antes de rodar, o que destruiria a transação explícita
    de `migrate()` e, com ela, a garantia de rollback."""
    return [s.strip() for s in script.strip().split(";") if s.strip()]


def _apply_migration_1(connection: sqlite3.Connection) -> None:
    for statement in _statements(_MIGRATION_1):
        connection.execute(statement)


def _apply_migration_2(connection: sqlite3.Connection) -> None:
    # `ALTER TABLE ... ADD COLUMN` falha se a coluna já existir; um banco
    # criado do zero por esta mesma migração nunca cai nesse caso, mas um
    # banco parcialmente migrado à mão poderia — checamos antes.
    existing = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    for statement in _statements(_MIGRATION_2_DDL):
        if statement.upper().startswith("ALTER TABLE USERS ADD COLUMN"):
            column = statement.split()[5]
            if column in existing:
                continue
        connection.execute(statement)
    _migrate_session_tokens(connection)


# Ordem importa: índice N aplica a migração que leva o schema de N para N+1.
_MIGRATIONS = (
    _apply_migration_1,
    _apply_migration_2,
)


def _current_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(connection: sqlite3.Connection) -> int:
    """Aplica as migrações pendentes, uma transação por migração. Devolve a
    versão final. Em caso de falha: rollback da migração que falhou (as
    anteriores, já commitadas, permanecem) e `MigrationError`."""
    version = _current_version(connection)
    if version > SCHEMA_VERSION:
        raise MigrationError(
            f"O banco está na versão {version}, mais nova que a suportada por este código "
            f"({SCHEMA_VERSION}). Atualize o JARVIS — o banco não foi alterado."
        )

    # Modo autocommit durante a migração para que `BEGIN`/`COMMIT`/`ROLLBACK`
    # explícitos funcionem sem colidir com a transação implícita que o
    # `sqlite3` abriria sozinho. Restaurado no `finally` — os repositórios
    # continuam com a semântica de sempre (`commit()` explícito).
    previous_isolation = connection.isolation_level
    connection.isolation_level = None
    try:
        while version < SCHEMA_VERSION:
            apply = _MIGRATIONS[version]
            target = version + 1
            logger.info("Migrando banco local: versão %s -> %s.", version, target)
            try:
                connection.execute("BEGIN")
                apply(connection)
                # `PRAGMA user_version` não aceita parâmetro ligado; `target` é
                # um int de um contador interno, nunca entrada do usuário.
                connection.execute(f"PRAGMA user_version = {int(target)}")
                connection.execute("COMMIT")
            except Exception as exc:
                connection.execute("ROLLBACK")
                logger.exception("Falha ao migrar o banco para a versão %s; alterações revertidas.", target)
                raise MigrationError(
                    f"Falha ao migrar o banco local para a versão {target}. "
                    "Nenhuma alteração foi aplicada; seus dados foram preservados."
                ) from exc
            version = target
    finally:
        connection.isolation_level = previous_isolation

    return version


def connect(db_path: Path) -> sqlite3.Connection:
    """Abre (criando se preciso) o banco local, garante o schema atual e
    devolve a conexão. Uma conexão por processo é suficiente aqui — SQLite
    serializa escrita internamente, e o volume de dados de um assistente
    pessoal é pequeno."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection
