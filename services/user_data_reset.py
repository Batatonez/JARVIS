"""Reset de contas locais — apaga TODOS os usuários e o que pertence a eles.

Usado por `jarvis delete all users` (ver `frontend/cli.py`). Operação
destrutiva e irreversível na prática, então tem duas travas:

1. **Backup obrigatório antes de qualquer DELETE.** Se o backup falhar, a
   operação aborta e nada é apagado (`BackupFailedError`).
2. A confirmação interativa (digitar `DELETE`) fica no CLI, não aqui —
   este módulo é a mecânica, não a política.

**Estratégia: `DELETE FROM users`, não apagar o arquivo do banco.** Todas
as tabelas de dado de usuário (`sessions`, `conversations`,
`email_verification_tokens`, `user_memories`) referenciam `users(id)` com
`ON DELETE CASCADE`, e `messages` referencia `conversations(id)` do mesmo
jeito — com `PRAGMA foreign_keys = ON` (garantido por `connect()`), apagar
os usuários leva o resto junto. Isso preserva o schema e o `user_version`,
então o banco continua válido e migrado; recriar o arquivo perderia isso e
exigiria remigrar do zero.

**O que NÃO é tocado**: `.env`, chaves de API, configuração de SMTP,
modelos de voz (`data/models/`), código, e qualquer coisa fora de `data/`
que pertença ao projeto (`.swarm`, Ruflo, etc.).
"""

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from services.local_database import connect

logger = logging.getLogger(__name__)

# Ordem deliberada: filhos antes dos pais. O CASCADE já daria conta, mas
# apagar explicitamente torna a operação correta mesmo que as foreign keys
# estejam desligadas por algum motivo — e deixa a contagem por tabela
# honesta no relatório.
#
# SQL literal (e não `f"DELETE FROM {table}"`) de propósito: a varredura
# estática de `tests/test_security_v1.py` proíbe query montada por
# formatação em `services/`. Os nomes aqui seriam constantes e seguros, mas
# manter a regra sem exceção vale mais do que a conveniência.
_DELETE_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("messages", "DELETE FROM messages"),
    ("conversations", "DELETE FROM conversations"),
    ("user_memories", "DELETE FROM user_memories"),
    ("email_verification_tokens", "DELETE FROM email_verification_tokens"),
    ("sessions", "DELETE FROM sessions"),
    ("users", "DELETE FROM users"),
)


class BackupFailedError(Exception):
    """O backup não pôde ser criado. Nada foi apagado."""


@dataclass(frozen=True)
class ResetSummary:
    backup_path: Path
    deleted: dict[str, int]
    memory_dirs_removed: int
    session_token_cleared: bool

    def total_rows(self) -> int:
        return sum(self.deleted.values())


def backup_database(db_path: Path, backups_dir: Path) -> Path:
    """Copia o banco para `backups_dir` com timestamp. Usa a API de backup
    do próprio SQLite (não `shutil.copy`): ela é consistente mesmo se o
    banco estiver sendo usado por outro processo — copiar o arquivo de um
    banco com transação em andamento pode gerar uma cópia corrompida.

    Levanta `BackupFailedError` em qualquer falha — quem chama deve abortar."""
    db_path = Path(db_path)
    backups_dir = Path(backups_dir)
    if not db_path.is_file():
        raise BackupFailedError(f"Banco não encontrado em {db_path}.")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = backups_dir / f"jarvis-before-delete-users-{timestamp}.db"
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(db_path))
        try:
            target = sqlite3.connect(str(destination))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    except Exception as exc:
        raise BackupFailedError(f"Não foi possível criar o backup do banco: {exc}") from exc

    if not destination.is_file() or destination.stat().st_size == 0:
        raise BackupFailedError("O backup ficou vazio ou não foi criado.")
    return destination


def delete_all_users(settings) -> ResetSummary:
    """Apaga todas as contas e tudo que pertence a elas. Faz backup antes;
    se o backup falhar, **nada** é apagado.

    O DELETE roda em uma transação: se algo falhar no meio, o banco volta
    ao estado anterior."""
    backup_path = backup_database(settings.db_path, settings.data_dir / "backups")
    logger.info("Backup criado antes do reset de contas: %s", backup_path.name)

    deleted: dict[str, int] = {}
    connection = connect(settings.db_path)
    try:
        connection.execute("BEGIN")
        try:
            for table, statement in _DELETE_STATEMENTS:
                cursor = connection.execute(statement)
                deleted[table] = cursor.rowcount if cursor.rowcount > 0 else 0
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()

    memory_dirs_removed = _remove_user_memory_dirs(settings.users_dir)
    session_token_cleared = _clear_session_token(settings.session_token_path)

    logger.info(
        "Reset de contas concluído: %s linhas removidas, %s pasta(s) de memória.",
        sum(deleted.values()),
        memory_dirs_removed,
    )
    return ResetSummary(
        backup_path=backup_path,
        deleted=deleted,
        memory_dirs_removed=memory_dirs_removed,
        session_token_cleared=session_token_cleared,
    )


def _remove_user_memory_dirs(users_dir: Path) -> int:
    """Remove `data/users/<user-id>/` — a memória em arquivo de cada conta
    (`profile.md`/`preferences.md`). Sem isto, uma conta nova recriada com
    o mesmo id (improvável, mas) herdaria memória alheia, e os arquivos
    ficariam órfãos para sempre.

    Só mexe DENTRO de `users_dir`; nunca em `data/models/` nem em `.env`."""
    users_dir = Path(users_dir)
    if not users_dir.is_dir():
        return 0
    removed = 0
    for entry in users_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            shutil.rmtree(entry)
            removed += 1
        except OSError:
            logger.warning("Não foi possível remover a pasta de memória %s.", entry.name)
    return removed


def _clear_session_token(session_token_path: Path) -> bool:
    """Apaga o token de sessão local. A sessão correspondente já foi
    removida do banco, então o arquivo só apontaria para algo inexistente —
    e o auto-login precisa parar de acontecer depois do reset."""
    path = Path(session_token_path)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        logger.warning("Não foi possível remover o token de sessão local.")
    return False
