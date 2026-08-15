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
SCHEMA_VERSION = 6


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


# --- v3 — v1.1: memória de longo prazo por usuário -------------------------
# Separada de `messages` de propósito: mensagem pertence a UMA conversa;
# memória pertence ao USUÁRIO e atravessa todas as conversas (ver
# services/long_term_memory.py e docs/architecture.md).
_MIGRATION_3_DDL = """
CREATE TABLE IF NOT EXISTS user_memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    -- Chave de deduplicação: normalização do conteúdo (ver
    -- LongTermMemoryRepository._dedup_key). Um UNIQUE por (user_id, dedup_key)
    -- é o que impede "Meu nome é Davi" virar 12 memórias iguais.
    dedup_key TEXT NOT NULL,
    source_conversation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_memories_dedup
    ON user_memories(user_id, dedup_key);
CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id);
"""


def _apply_migration_3(connection: sqlite3.Connection) -> None:
    for statement in _statements(_MIGRATION_3_DDL):
        connection.execute(statement)


# --- v4 — v1.3: identidade única, 2FA, sessões com metadata, preferências ---
#
# `normalized_username`/`normalized_email` existem como COLUNAS próprias em vez
# de um índice sobre `LOWER(email)`: com a coluna, `email` volta a guardar o
# que o usuário digitou (para exibir com a caixa original) e a unicidade passa
# a ser um dado explícito, indexável e testável. O índice antigo
# `idx_users_email_unique` é substituído porque o novo é estritamente mais
# rigoroso — ele pega "DAVI@x.com" vs "davi@x.com", que o antigo deixava passar.
_MIGRATION_4_DDL = """
ALTER TABLE users ADD COLUMN normalized_username TEXT;
ALTER TABLE users ADD COLUMN normalized_email TEXT;
ALTER TABLE users ADD COLUMN totp_secret TEXT;
ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN totp_confirmed_at TEXT;
ALTER TABLE users ADD COLUMN totp_failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN totp_lockout_until TEXT;

ALTER TABLE conversations ADD COLUMN manual_title INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sessions ADD COLUMN last_used_at TEXT;
ALTER TABLE sessions ADD COLUMN platform TEXT;
ALTER TABLE sessions ADD COLUMN device_label TEXT;

-- Preferências por conta (ex.: microfone escolhido). Chave/valor de propósito:
-- evita uma migração nova a cada preferência que o HUD passar a lembrar.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- Códigos de recuperação de 2FA. Só o HASH é guardado (mesmo tratamento de
-- senha); `used_at` implementa o uso único.
CREATE TABLE IF NOT EXISTS recovery_codes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_recovery_codes_user ON recovery_codes(user_id);

-- Troca de e-mail em duas fases: o e-mail ATUAL continua valendo até o código
-- enviado ao NOVO endereço ser confirmado (item 41).
CREATE TABLE IF NOT EXISTS pending_email_changes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    new_email TEXT NOT NULL,
    normalized_new_email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resend_available_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_email_user ON pending_email_changes(user_id);
"""

_MIGRATION_4_INDEXES = """
DROP INDEX IF EXISTS idx_users_email_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_normalized_username
    ON users(normalized_username);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_normalized_email
    ON users(normalized_email) WHERE normalized_email IS NOT NULL;
"""


class IdentityConflictError(MigrationError):
    """Duas contas dividem o mesmo e-mail/username (comparação
    case-insensitive). O item 62 da v1.3 é explícito: **parar e reportar** —
    nunca apagar conta, nunca fundir contas, nunca reescrever o e-mail de
    alguém automaticamente para "resolver" o conflito."""

    def __init__(self, kind: str, conflicts: list[tuple[str, int]]) -> None:
        self.kind = kind
        self.conflicts = conflicts
        detail = ", ".join(f"{value!r} ({count} contas)" for value, count in conflicts)
        super().__init__(
            f"Não é possível aplicar a unicidade de {kind}: existem duplicatas no banco — {detail}. "
            "Nenhuma alteração foi feita. Resolva manualmente qual conta fica com cada "
            f"{kind} antes de atualizar o JARVIS."
        )


def find_identity_conflicts(connection: sqlite3.Connection) -> dict[str, list[tuple[str, int]]]:
    """Duplicatas case-insensitive de username e e-mail. Somente leitura —
    pode ser chamada a qualquer momento para diagnóstico."""
    username_rows = connection.execute(
        "SELECT LOWER(TRIM(username)) AS value, COUNT(*) AS total FROM users "
        "GROUP BY value HAVING total > 1"
    ).fetchall()
    email_rows = connection.execute(
        "SELECT LOWER(TRIM(email)) AS value, COUNT(*) AS total FROM users "
        "WHERE email IS NOT NULL AND TRIM(email) <> '' "
        "GROUP BY value HAVING total > 1"
    ).fetchall()
    return {
        "username": [(row["value"], row["total"]) for row in username_rows],
        "email": [(row["value"], row["total"]) for row in email_rows],
    }


def _apply_migration_4(connection: sqlite3.Connection) -> None:
    existing_users = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    existing_conversations = {
        row["name"] for row in connection.execute("PRAGMA table_info(conversations)")
    }
    existing_sessions = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
    already = {
        "users": existing_users,
        "conversations": existing_conversations,
        "sessions": existing_sessions,
    }

    for statement in _statements(_MIGRATION_4_DDL):
        upper = statement.upper()
        if upper.startswith("ALTER TABLE"):
            parts = statement.split()
            table, column = parts[2], parts[5]
            if column in already.get(table, set()):
                continue
        connection.execute(statement)

    # Backfill ANTES dos índices: sem isto, todas as linhas ficariam com
    # `normalized_username` NULL e o índice único não protegeria nada.
    connection.execute("UPDATE users SET normalized_username = LOWER(TRIM(username))")
    connection.execute(
        "UPDATE users SET normalized_email = LOWER(TRIM(email)) "
        "WHERE email IS NOT NULL AND TRIM(email) <> ''"
    )

    # Item 62: checar conflitos legados antes de tentar criar o índice. Sem
    # isto, o SQLite abortaria a migração com um "UNIQUE constraint failed"
    # cru, sem dizer QUAL e-mail está duplicado nem o que fazer a respeito.
    conflicts = find_identity_conflicts(connection)
    if conflicts["username"]:
        raise IdentityConflictError("username", conflicts["username"])
    if conflicts["email"]:
        raise IdentityConflictError("e-mail", conflicts["email"])

    for statement in _statements(_MIGRATION_4_INDEXES):
        connection.execute(statement)


def _statements(script: str) -> list[str]:
    """Divide um script DDL em statements.

    NÃO usamos `executescript()`: ele emite um COMMIT implícito antes de
    rodar, o que destruiria a transação explícita de `migrate()` e, com ela,
    a garantia de rollback.

    **Comentários são removidos antes do split** (v1.3). A versão anterior
    fazia `split(";")` no texto cru, então um `;` dentro de um comentário
    `--` partia o script no meio e produzia um "statement" que era o resto da
    frase em português — erro de sintaxe do SQLite em tempo de migração,
    descoberto exatamente assim. Continua sem suporte a `;` dentro de literal
    de string ou corpo de trigger, o que estes scripts não usam."""
    without_comments = "\n".join(
        line.split("--", 1)[0] if "--" in line else line for line in script.splitlines()
    )
    return [s.strip() for s in without_comments.strip().split(";") if s.strip()]


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


# --- v5 — v1.5: histórico de atividade de segurança ------------------------
#
# Tabela nova em vez de reaproveitar `user_settings` (chave/valor) porque isto
# é uma série temporal com consulta própria (últimos N eventos de um usuário),
# não uma preferência.
#
# `safe_metadata_json` é NULL na maioria dos eventos e, quando existe, guarda
# só chaves de uma allowlist fechada (ver `services/security_event_repository.py`,
# `_ALLOWED_METADATA_KEYS`). Nunca senha, token, API key, segredo TOTP, código
# de recuperação em claro, prompt ou conteúdo de conversa — a coluna é
# deliberadamente estreita para não virar um depósito de "qualquer coisa que
# parecia útil na hora".
_MIGRATION_5_DDL = """
CREATE TABLE IF NOT EXISTS security_events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    safe_metadata_json TEXT
);

-- (user_id, created_at) porque toda leitura é "os eventos DESTA conta, mais
-- recentes primeiro" — nunca uma varredura global.
CREATE INDEX IF NOT EXISTS idx_security_events_user
    ON security_events(user_id, created_at DESC);
"""


def _apply_migration_5(connection: sqlite3.Connection) -> None:
    for statement in _statements(_MIGRATION_5_DDL):
        connection.execute(statement)


# --- v6 — v1.8: índice local de arquivos ----------------------------------
#
# O índice NÃO é por usuário: os arquivos são da máquina, e indexar a mesma
# pasta uma vez por conta duplicaria trabalho e disco sem nenhum ganho. As
# RAÍZES indexadas, sim, são por conta — cada pessoa escolhe onde o JARVIS
# pode procurar.
#
# `file_content_fts` é uma tabela FTS5 externa (`content=''`): ela guarda só
# o índice invertido, não uma segunda cópia do texto. Isso importa num
# assistente pessoal — duplicar o conteúdo de Documents inteiro dentro do
# banco seria um passivo de disco e de privacidade.
_MIGRATION_6_DDL = """
CREATE TABLE IF NOT EXISTS indexed_roots (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_indexed_roots_unique
    ON indexed_roots(user_id, path);

CREATE TABLE IF NOT EXISTS file_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    -- Nome sem acento e em minúsculas: é por ele que a busca casa, para
    -- "historia" achar "História" sem depender de o usuário digitar o acento.
    normalized_name TEXT NOT NULL,
    extension TEXT NOT NULL DEFAULT '',
    parent TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    modified_at TEXT NOT NULL,
    is_directory INTEGER NOT NULL DEFAULT 0,
    root_path TEXT NOT NULL DEFAULT '',
    -- Marca da última varredura que viu este arquivo. É o que permite a
    -- reindexação remover o que sumiu do disco sem apagar a tabela inteira.
    indexed_at TEXT NOT NULL,
    content_indexed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_file_index_name ON file_index(normalized_name);
CREATE INDEX IF NOT EXISTS idx_file_index_extension ON file_index(extension);
CREATE INDEX IF NOT EXISTS idx_file_index_modified ON file_index(modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_index_root ON file_index(root_path);

CREATE TABLE IF NOT EXISTS file_index_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# FTS5 fica separado porque pode não existir: um SQLite compilado sem a
# extensão levantaria erro na criação da tabela. Sem FTS5 o JARVIS perde a
# busca por CONTEÚDO e mantém tudo o mais (nome, metadata) — degradação
# aceitável, e muito melhor que uma migração que falha e trava o app.
_MIGRATION_6_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS file_content_fts USING fts5(
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def _apply_migration_6(connection: sqlite3.Connection) -> None:
    for statement in _statements(_MIGRATION_6_DDL):
        connection.execute(statement)
    try:
        for statement in _statements(_MIGRATION_6_FTS):
            connection.execute(statement)
    except sqlite3.OperationalError as exc:
        # Sem FTS5: registra e segue. A busca por conteúdo se desativa
        # sozinha (ver services/files/file_index.py::content_search_available).
        logger.warning("FTS5 indisponível; busca por conteúdo ficará desativada. (%s)", exc)


def has_fts5(connection: sqlite3.Connection) -> bool:
    """A busca por conteúdo existe neste banco? Consultado em runtime em vez
    de presumido: o mesmo código roda em máquinas com SQLite diferentes."""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'file_content_fts'"
    ).fetchone()
    return row is not None


# Ordem importa: índice N aplica a migração que leva o schema de N para N+1.
_MIGRATIONS = (
    _apply_migration_1,
    _apply_migration_2,
    _apply_migration_3,
    _apply_migration_4,
    _apply_migration_5,
    _apply_migration_6,
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
            except IdentityConflictError:
                # Repassado INTACTO: a mensagem diz qual e-mail/username está
                # duplicado e o que fazer a respeito (item 62 da v1.3).
                # Envolvê-la no erro genérico apagaria justamente a parte
                # acionável e deixaria o usuário sem saber o que resolver.
                connection.execute("ROLLBACK")
                logger.warning("Migração abortada por conflito de identidade; banco preservado.")
                raise
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
