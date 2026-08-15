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

**v1.3**: metadata para a tela "Active Sessions" (`created_at`,
`last_used_at`, plataforma e um rótulo de dispositivo). O que a tela mostra é
deliberadamente pobre — nada de IP, geolocalização ou fingerprint: um
assistente local não tem essa informação de verdade e inventá-la daria uma
falsa sensação de auditoria. E **nunca** o token, nem parte dele (item 54).
"""

import hashlib
import platform
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_TOKEN_BYTES = 32
_DEFAULT_TTL_DAYS = 30
_MAX_LABEL_LENGTH = 80


def hash_token(token: str) -> str:
    """Hash determinístico do token, usado como chave primária de `sessions`.
    Determinístico de propósito: precisamos procurar por ele."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def describe_current_device() -> tuple[str, str]:
    """`(plataforma, rótulo)` desta máquina, para a lista de sessões ficar
    reconhecível. Só dados de ambiente já disponíveis — nada é coletado nem
    enviado para lugar nenhum, e nada disso vira memória pessoal (ver
    CLAUDE.md, "Metadados técnicos não são memória pessoal")."""
    try:
        system = platform.system() or "?"
        release = platform.release() or ""
        node = platform.node() or ""
    except Exception:
        return ("?", "Este dispositivo")
    label = f"{node} · {system} {release}".strip(" ·")
    return (f"{system} {release}".strip(), label[:_MAX_LABEL_LENGTH] or "Este dispositivo")


@dataclass(frozen=True)
class SessionInfo:
    """Uma linha da tela "Active Sessions". Note o que NÃO existe aqui:
    nenhum campo carrega o token nem seu hash — `is_current` é resolvido no
    repositório, comparando hashes internamente."""

    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    platform: str
    device_label: str
    is_current: bool
    session_id: str


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
        platform_name, device_label = describe_current_device()
        self._conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_used_at, "
            "                      platform, device_label) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                hash_token(token),
                user_id,
                now.isoformat(),
                expires_at.isoformat(),
                now.isoformat(),
                platform_name,
                device_label,
            ),
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

    def touch(self, token: str) -> None:
        """Atualiza `last_used_at`. Chamado no auto-login, não a cada
        mensagem: a granularidade útil para o usuário é "quando este
        dispositivo entrou pela última vez", e escrever no banco a cada
        interação seria custo sem informação nova."""
        if not token:
            return
        self._conn.execute(
            "UPDATE sessions SET last_used_at = ? WHERE token_hash = ?",
            (datetime.now(timezone.utc).isoformat(), hash_token(token)),
        )
        self._conn.commit()

    def list_sessions(self, user_id: str, *, current_token: str | None = None) -> list[SessionInfo]:
        """Sessões vivas da conta, mais recentes primeiro. Expiradas são
        filtradas na leitura (e não apagadas aqui: limpeza é efeito colateral
        de `validate_session`, e uma listagem deve ser só leitura)."""
        current_hash = hash_token(current_token) if current_token else None
        rows = self._conn.execute(
            "SELECT token_hash, created_at, expires_at, last_used_at, platform, device_label "
            "FROM sessions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()

        now = datetime.now(timezone.utc)
        sessions: list[SessionInfo] = []
        for row in rows:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                continue
            last_used = row["last_used_at"]
            sessions.append(
                SessionInfo(
                    created_at=datetime.fromisoformat(row["created_at"]),
                    last_used_at=datetime.fromisoformat(last_used) if last_used else None,
                    expires_at=expires_at,
                    platform=row["platform"] or "?",
                    device_label=row["device_label"] or "Dispositivo desconhecido",
                    is_current=(current_hash is not None and row["token_hash"] == current_hash),
                    # Identificador opaco e derivado, só para a UI ter uma
                    # chave de lista — não serve para autenticar nada, e não
                    # permite reconstruir o token (é um prefixo do HASH).
                    session_id=row["token_hash"][:16],
                )
            )
        return sessions

    def delete_session(self, token: str) -> None:
        self._delete_by_hash(hash_token(token))

    def delete_all_for_user(self, user_id: str) -> int:
        """Revoga TODAS as sessões de um usuário. Devolve quantas foram."""
        cursor = self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._conn.commit()
        return cursor.rowcount

    def delete_by_session_id(self, user_id: str, session_id: str) -> bool:
        """Revoga UMA sessão pelo identificador opaco que a tela mostra
        (v1.5.0). Devolve `True` se alguma linha foi removida.

        O `user_id` entra no WHERE, e é ele que impede esta operação de virar
        um caminho para derrubar a sessão de outra conta: mesmo recebendo um
        `session_id` adivinhado de outro usuário, o DELETE não casa. O
        `user_id` nunca vem do frontend — quem chama é o `AccountManager`, com
        a conta autenticada (ver `app/account_manager.py`).

        `session_id` é o prefixo do SHA-256 do token (nunca o token), então
        casamos por prefixo — é o mesmo valor que `list_sessions` devolveu."""
        if not session_id:
            return False
        cursor = self._conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND substr(token_hash, 1, ?) = ?",
            (user_id, len(session_id), session_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def session_id_for_token(self, token: str) -> str:
        """Identificador opaco da sessão de um token — o mesmo valor que
        `list_sessions` expõe. Serve para o chamador saber se a sessão que
        acabou de ser revogada era a atual (e, nesse caso, deslogar)."""
        return hash_token(token)[:16] if token else ""

    def delete_others_for_user(self, user_id: str, *, keep_token: str) -> int:
        """"Sair das outras sessões" (item 55): revoga tudo menos a atual.

        O escopo por `user_id` é o que impede esta operação de virar um
        caminho para derrubar a sessão de outra conta."""
        cursor = self._conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
            (user_id, hash_token(keep_token)),
        )
        self._conn.commit()
        return cursor.rowcount

    def _delete_by_hash(self, token_hash: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self._conn.commit()
