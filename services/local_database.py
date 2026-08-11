"""Conexão SQLite local + schema — `sqlite3` da biblioteca padrão, sem ORM.

Única porta de entrada para o banco: `user_repository.py`,
`session_repository.py` e `conversation_repository.py` chamam `connect()` e
fazem suas próprias queries, sempre parametrizadas (`?`) — nunca
interpolação de string, para não abrir espaço a SQL injection. O frontend
(QML/Bridge) nunca importa este módulo nem sabe que SQLite existe.

O banco vive em `settings.db_path` (`data/jarvis.db`), fora do Git (ver
`.gitignore`) — contém contas, sessões e conversas, tudo dado pessoal.
"""

import sqlite3
from pathlib import Path

_SCHEMA = """
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


def connect(db_path: Path) -> sqlite3.Connection:
    """Abre (criando se preciso) o banco local e garante o schema. Uma
    conexão por processo é suficiente aqui — SQLite serializa escrita
    internamente, e o volume de dados de um assistente pessoal é pequeno."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    connection.commit()
    return connection
