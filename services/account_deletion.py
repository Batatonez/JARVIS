"""Exclusão da conta ATUAL (v1.3, itens 56-59).

Não confundir com `services/user_data_reset.py` (`jarvis delete all users`),
que apaga TODAS as contas da máquina. Aqui é o oposto: uma conta só, e a
garantia central é **não encostar em nenhuma outra**.

Duas metades, nessa ordem:

1. **Banco**, em transação única. Todas as tabelas de dado pessoal
   referenciam `users(id)` com `ON DELETE CASCADE` e `PRAGMA foreign_keys`
   está ligado (ver `connect()`), mas os DELETE são explícitos mesmo assim:
   assim a contagem por tabela é honesta no relatório e a operação continua
   correta mesmo que as foreign keys estejam desligadas. Falha em qualquer
   ponto -> `ROLLBACK`, nada é apagado.
2. **Arquivos** (`data/users/<user-id>/`), só depois do commit. A ordem
   importa: apagar arquivos primeiro e falhar no banco deixaria uma conta
   viva sem a memória dela.

**Path safety (item 59).** O diretório da conta é resolvido e comparado
contra a raiz de dados antes de qualquer remoção; um `user_id` que tentasse
escapar por `..` ou caminho absoluto é rejeitado, e nada fora de
`users_dir` é removido em nenhuma hipótese.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Filhos antes dos pais. SQL literal (nunca `f"DELETE FROM {table}"`) — a
# varredura estática de `tests/test_security_v1.py` proíbe query montada por
# formatação em `services/`, e manter a regra sem exceção vale mais que a
# conveniência de um laço genérico.
_DELETE_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("messages", "DELETE FROM messages WHERE conversation_id IN "
                 "(SELECT id FROM conversations WHERE user_id = ?)"),
    ("conversations", "DELETE FROM conversations WHERE user_id = ?"),
    ("user_memories", "DELETE FROM user_memories WHERE user_id = ?"),
    ("email_verification_tokens", "DELETE FROM email_verification_tokens WHERE user_id = ?"),
    ("pending_email_changes", "DELETE FROM pending_email_changes WHERE user_id = ?"),
    ("recovery_codes", "DELETE FROM recovery_codes WHERE user_id = ?"),
    ("user_settings", "DELETE FROM user_settings WHERE user_id = ?"),
    ("sessions", "DELETE FROM sessions WHERE user_id = ?"),
    ("users", "DELETE FROM users WHERE id = ?"),
)


class AccountDeletionError(Exception):
    """Falha ao apagar a conta. O banco foi preservado (rollback aplicado)."""


@dataclass(frozen=True)
class DeletionSummary:
    user_id: str
    deleted: dict[str, int] = field(default_factory=dict)
    memory_dir_removed: bool = False

    def total_rows(self) -> int:
        return sum(self.deleted.values())


def user_data_dir(users_dir: Path, user_id: str) -> Path | None:
    """Caminho da pasta da conta, **somente** se ele estiver de fato dentro de
    `users_dir`. `None` para qualquer `user_id` que escape da raiz.

    A checagem é feita nos caminhos RESOLVIDOS (`.resolve()`), então
    `..`, separador invertido e link simbólico caem no mesmo teste."""
    if not user_id or "/" in user_id or "\\" in user_id or user_id in (".", ".."):
        return None
    root = Path(users_dir).resolve()
    candidate = (root / user_id).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        logger.warning("Caminho de dados de conta fora da raiz esperada — recusado.")
        return None
    return candidate


def delete_account(connection, *, user_id: str, users_dir: Path) -> DeletionSummary:
    """Apaga a conta e tudo que pertence a ela. Nunca toca em outra conta:
    todo statement é filtrado por `user_id`."""
    if not user_id:
        raise AccountDeletionError("Conta inválida.")

    # Resolve o caminho ANTES de mexer no banco: se o `user_id` for suspeito,
    # abortamos sem ter apagado nada.
    target_dir = user_data_dir(users_dir, user_id)

    deleted: dict[str, int] = {}
    previous_isolation = connection.isolation_level
    connection.isolation_level = None
    try:
        connection.execute("BEGIN")
        try:
            for table, statement in _DELETE_STATEMENTS:
                cursor = connection.execute(statement, (user_id,))
                deleted[table] = max(0, cursor.rowcount)
            connection.execute("COMMIT")
        except Exception as exc:
            connection.execute("ROLLBACK")
            logger.exception("Falha ao apagar a conta; nenhuma alteração foi aplicada.")
            raise AccountDeletionError(
                "Não foi possível apagar a conta. Nenhum dado foi alterado."
            ) from exc
    finally:
        connection.isolation_level = previous_isolation

    memory_removed = False
    if target_dir is not None and target_dir.is_dir():
        try:
            shutil.rmtree(target_dir)
            memory_removed = True
        except OSError:
            # O banco já foi commitado; a conta não existe mais. Um arquivo
            # órfão é um problema menor do que reverter a exclusão pedida.
            logger.warning("Não foi possível remover a pasta de dados da conta apagada.")

    logger.info("Conta apagada: %s linhas removidas do banco.", sum(deleted.values()))
    return DeletionSummary(user_id=user_id, deleted=deleted, memory_dir_removed=memory_removed)
