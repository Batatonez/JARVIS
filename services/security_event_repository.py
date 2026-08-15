"""Histórico de atividade de segurança da conta (v1.5.0).

Responde à pergunta "o que aconteceu de importante nesta conta?" — entrei de
onde, quando a senha mudou, quando o 2FA foi ligado, qual sessão foi
encerrada. É o que permite o dono perceber atividade que não foi dele.

--------------------------------------------------------------------------
O que NUNCA entra aqui — regra permanente, não uma escolha desta versão
--------------------------------------------------------------------------
Senha (em qualquer forma), token de sessão (nem o hash, nem um prefixo), API
key, header `Authorization`, segredo TOTP, código de recuperação em claro,
prompt, conteúdo de conversa, ou qualquer dado pessoal que o evento não
precise para ser compreensível.

Isso é imposto por construção, não por disciplina: `record()` só aceita um
`SecurityEventType` da enumeração e só grava metadata cujas chaves estejam em
`_ALLOWED_METADATA_KEYS`. Uma chave fora da lista é descartada com aviso —
nunca gravada "por via das dúvidas". Se um evento novo precisar de um campo
novo, a allowlist precisa ser editada de propósito, o que é exatamente o
ponto de revisão que se quer ter.

--------------------------------------------------------------------------
Visibilidade e retenção
--------------------------------------------------------------------------
Toda leitura é escopada por `user_id` — não existe método que liste eventos
sem filtrar por conta, então não há caminho pelo qual um usuário veja
atividade de outro. A retenção é dupla: `MAX_EVENTS_PER_USER` (a poda roda
depois de cada gravação) e `RETENTION_DAYS`. Um log de segurança que cresce
para sempre num banco local é só um passivo.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_USER = 200
RETENTION_DAYS = 180
DEFAULT_PAGE_SIZE = 50


class SecurityEventType(Enum):
    """Enumeração fechada — um tipo de evento novo tem que ser adicionado
    aqui de propósito, e é aí que se decide se ele é seguro de registrar."""

    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_BLOCKED = "login_blocked"
    PASSWORD_CHANGED = "password_changed"
    EMAIL_CHANGE_REQUESTED = "email_change_requested"
    EMAIL_CHANGED = "email_changed"
    TWO_FACTOR_ENABLED = "two_factor_enabled"
    TWO_FACTOR_DISABLED = "two_factor_disabled"
    RECOVERY_CODE_USED = "recovery_code_used"
    SESSION_REVOKED = "session_revoked"
    OTHER_SESSIONS_REVOKED = "other_sessions_revoked"
    ACCOUNT_DELETION_STARTED = "account_deletion_started"
    ACCOUNT_DELETION_COMPLETED = "account_deletion_completed"


# Texto exibido no HUD. Fica aqui (e não em QML) porque a lista de tipos e a
# lista de rótulos precisam andar juntas — um tipo sem rótulo apareceria como
# um identificador cru para o usuário.
_LABELS: dict[SecurityEventType, str] = {
    SecurityEventType.LOGIN_SUCCEEDED: "Entrada na conta",
    SecurityEventType.LOGIN_BLOCKED: "Tentativa de entrada bloqueada",
    SecurityEventType.PASSWORD_CHANGED: "Senha alterada",
    SecurityEventType.EMAIL_CHANGE_REQUESTED: "Troca de e-mail solicitada",
    SecurityEventType.EMAIL_CHANGED: "E-mail alterado",
    SecurityEventType.TWO_FACTOR_ENABLED: "Verificação em duas etapas ativada",
    SecurityEventType.TWO_FACTOR_DISABLED: "Verificação em duas etapas desativada",
    SecurityEventType.RECOVERY_CODE_USED: "Código de recuperação usado",
    SecurityEventType.SESSION_REVOKED: "Sessão encerrada",
    SecurityEventType.OTHER_SESSIONS_REVOKED: "Outras sessões encerradas",
    SecurityEventType.ACCOUNT_DELETION_STARTED: "Exclusão de conta iniciada",
    SecurityEventType.ACCOUNT_DELETION_COMPLETED: "Exclusão de conta concluída",
}

# Allowlist fechada de chaves de metadata. Cada uma existe porque um evento
# específico fica incompreensível sem ela — não porque "pode ser útil".
#
#   platform / device_label   de qual máquina (dado que a própria sessão já
#                             guarda; ver services/session_repository.py — não
#                             é fingerprint, não é IP, não é geolocalização)
#   channel                   "password" / "two_factor" / "recovery_code"
#   session_count             quantas sessões foram encerradas de uma vez
#   masked_email              e-mail já mascarado (d***@e***.com), nunca o
#                             endereço completo
#   codes_remaining           quantos códigos de recuperação restaram
_ALLOWED_METADATA_KEYS = frozenset(
    {"platform", "device_label", "channel", "session_count", "masked_email", "codes_remaining"}
)
_MAX_METADATA_VALUE_LENGTH = 120


@dataclass(frozen=True)
class SecurityEvent:
    event_id: str
    event_type: SecurityEventType
    created_at: datetime
    metadata: dict[str, str | int]

    @property
    def label(self) -> str:
        return _LABELS.get(self.event_type, self.event_type.value)


def sanitize_metadata(metadata: dict | None) -> dict[str, str | int]:
    """Mantém só chaves da allowlist, com valores curtos e de tipo simples.

    Função de módulo (e não método privado) de propósito: é o ponto que os
    testes verificam diretamente para provar que uma chave como `password` ou
    `token` nunca sobrevive, independentemente de quem chamou `record()`."""
    if not metadata:
        return {}
    clean: dict[str, str | int] = {}
    for key, value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            logger.warning("Metadata '%s' fora da allowlist de eventos de segurança — descartada.", key)
            continue
        if isinstance(value, bool):
            # `bool` é subclasse de `int` em Python; guardar como texto evita
            # uma leitura ambígua ("1" era True ou a contagem 1?).
            clean[key] = "sim" if value else "não"
        elif isinstance(value, int):
            clean[key] = value
        elif value is None:
            continue
        else:
            clean[key] = str(value)[:_MAX_METADATA_VALUE_LENGTH]
    return clean


class SecurityEventRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def record(
        self,
        *,
        user_id: str,
        event_type: SecurityEventType,
        metadata: dict | None = None,
    ) -> None:
        """Grava um evento. Nunca levanta para o chamador: registrar
        atividade é observabilidade, e uma falha ao anotar o que aconteceu não
        pode derrubar a operação que aconteceu (trocar a senha tem que
        funcionar mesmo que o log falhe)."""
        safe = sanitize_metadata(metadata)
        try:
            self._conn.execute(
                "INSERT INTO security_events (event_id, user_id, event_type, created_at, safe_metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    user_id,
                    event_type.value,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(safe, ensure_ascii=False) if safe else None,
                ),
            )
            self._conn.commit()
            self._prune(user_id)
        except sqlite3.Error:
            logger.exception("Falha ao registrar evento de segurança; a operação em si não foi afetada.")

    def list_events(
        self, user_id: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> list[SecurityEvent]:
        """Eventos DESTA conta, mais recentes primeiro. Não existe (e não deve
        passar a existir) uma variante sem `user_id`."""
        limit = max(1, min(int(limit), MAX_EVENTS_PER_USER))
        rows = self._conn.execute(
            "SELECT event_id, event_type, created_at, safe_metadata_json FROM security_events "
            "WHERE user_id = ? ORDER BY created_at DESC, event_id DESC LIMIT ? OFFSET ?",
            (user_id, limit, max(0, int(offset))),
        ).fetchall()

        events: list[SecurityEvent] = []
        for row in rows:
            try:
                event_type = SecurityEventType(row["event_type"])
            except ValueError:
                # Evento gravado por uma versão mais nova do JARVIS: ignorado
                # em vez de quebrar a tela inteira.
                continue
            raw_metadata = row["safe_metadata_json"]
            try:
                metadata = json.loads(raw_metadata) if raw_metadata else {}
            except json.JSONDecodeError:
                metadata = {}
            events.append(
                SecurityEvent(
                    event_id=row["event_id"],
                    event_type=event_type,
                    created_at=datetime.fromisoformat(row["created_at"]),
                    # Sanitiza também na LEITURA: uma linha gravada por uma
                    # versão anterior (ou editada à mão no banco) nunca chega
                    # à UI com uma chave fora da allowlist.
                    metadata=sanitize_metadata(metadata),
                )
            )
        return events

    def count_events(self, user_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total FROM security_events WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["total"]) if row else 0

    def _prune(self, user_id: str) -> None:
        """Retenção por idade E por quantidade. Roda depois de cada gravação,
        escopada ao usuário que acabou de gerar um evento — nunca varre a
        tabela inteira."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        self._conn.execute(
            "DELETE FROM security_events WHERE user_id = ? AND created_at < ?", (user_id, cutoff)
        )
        self._conn.execute(
            "DELETE FROM security_events WHERE user_id = ? AND event_id NOT IN ("
            "    SELECT event_id FROM security_events WHERE user_id = ? "
            "    ORDER BY created_at DESC, event_id DESC LIMIT ?"
            ")",
            (user_id, user_id, MAX_EVENTS_PER_USER),
        )
        self._conn.commit()
