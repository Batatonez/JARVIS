"""Repositório de contas locais — único módulo que lê/escreve a tabela
`users` e o único lugar que toca `password_hash`/`totp_secret`.
`app.models.User` (o tipo que sai daqui para o resto do sistema) nunca
carrega segredo nenhum.

**v1.0**: e-mail, flag de verificação, backoff de força bruta no login.

**v1.3**: identidade única de verdade e perfil editável.

- `normalized_username`/`normalized_email` são colunas próprias, com índice
  UNIQUE (ver `services/local_database.py`, migração 4). `username`/`email`
  guardam o que o usuário digitou (para exibir "BatatoNez"), a coluna
  normalizada é a que decide conflito — então "BatatoNez" e "batatonez" são
  a MESMA conta, e "DAVI@x.com" e "davi@x.com" o MESMO e-mail.
- `authenticate()` aceita username OU e-mail no mesmo campo (item 38).
- Trocar username/e-mail nunca mexe em `id`: chats, memória e sessões
  apontam para `user_id`, então renomear não quebra nada (item 40).
"""

import logging
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Plan, User
from services.password_hashing import hash_password, verify_password

logger = logging.getLogger(__name__)

# Backoff de login: a partir da 5ª falha consecutiva, cada nova falha
# bloqueia por um tempo que dobra (30s, 60s, 120s...), com teto de 15min.
# Nunca é permanente — um bloqueio permanente transformaria a proteção em
# uma negação de serviço contra o próprio dono da conta.
_FAILURES_BEFORE_LOCKOUT = 5
_BASE_LOCKOUT_SECONDS = 30
_MAX_LOCKOUT_SECONDS = 15 * 60

# Regras de username (item 33). Charset restrito de propósito: um username é
# identificador, aparece em log e é comparado — permitir espaço, emoji ou
# caractere de controle só cria ambiguidade e superfície de homoglyph.
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

DISPLAY_NAME_MAX_LENGTH = 64
EMAIL_MAX_LENGTH = 254  # RFC 5321 §4.5.3.1.3
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

# Hash descartável usado para não vazar por timing que um usuário não existe.
_DUMMY_HASH = "scrypt$32768$8$1$00$00"


class UsernameAlreadyExistsError(Exception):
    """O username (normalizado) já pertence a outra conta."""


class EmailAlreadyRegisteredError(Exception):
    """O e-mail (normalizado) já pertence a outra conta.

    Mensagem deliberadamente genérica: confirmar "este e-mail existe" enumera
    contas alheias (item 36/39). O username é diferente — é escolhido
    publicamente e precisa de erro específico para o cadastro ser usável."""

    code = "EMAIL_ALREADY_IN_USE"


class InvalidCredentialsError(Exception):
    """Credenciais inválidas — deliberadamente a mesma exceção e a mesma
    mensagem para "não existe" e "senha errada" (item 39)."""


class AccountLockedError(Exception):
    """Tentativas demais em sequência; a conta está em cooldown temporário."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Muitas tentativas de login. Tente novamente em {retry_after_seconds} segundos."
        )


class InvalidUsernameError(ValueError):
    """Username fora das regras — mensagem específica, é entrada do usuário."""


class InvalidEmailError(ValueError):
    """E-mail com formato inválido."""


# ----------------------------------------------------------------------
# Normalização e validação de identidade
# ----------------------------------------------------------------------


def _strip_control_chars(value: str) -> str:
    """Remove categoria Unicode "C" (controle, formato, surrogates). Pega
    coisas invisíveis que quebram log e comparação — incluindo RTL override,
    zero-width space e afins."""
    return "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")


def normalize_username(username: str) -> str:
    """Chave de conflito do username: sem espaços nas pontas, minúsculo.
    Também é o que `authenticate()` usa para procurar."""
    return _strip_control_chars(username or "").strip().lower()


def normalize_email(email: str) -> str:
    """Normalização conservadora: remove espaços e caixa. Não mexe em
    sub-endereçamento (`+tag`) nem em pontos do Gmail (item 35) — regras de
    provider específico não pertencem a uma normalização genérica, e
    removê-las impediria endereços legítimos de coexistirem."""
    return _strip_control_chars(email or "").strip().lower()


def validate_username(username: str) -> str:
    """Devolve o username já limpo (preservando a caixa digitada) ou levanta
    `InvalidUsernameError`."""
    cleaned = _strip_control_chars(username or "").strip()
    normalized = cleaned.lower()
    if len(normalized) < USERNAME_MIN_LENGTH:
        raise InvalidUsernameError(
            f"O username precisa ter pelo menos {USERNAME_MIN_LENGTH} caracteres."
        )
    if len(normalized) > USERNAME_MAX_LENGTH:
        raise InvalidUsernameError(
            f"O username pode ter no máximo {USERNAME_MAX_LENGTH} caracteres."
        )
    if not _USERNAME_PATTERN.match(normalized):
        raise InvalidUsernameError(
            "O username pode usar apenas letras, números, ponto, hífen e underline, "
            "e precisa começar com letra ou número."
        )
    return cleaned


def validate_display_name(display_name: str, *, fallback: str = "") -> str:
    """Nome de exibição é só visual (item 32): não é único, não é chave, não
    é usado em nenhuma decisão de segurança. Ainda assim é sanitizado e tem
    tamanho limitado — ele aparece na UI e em e-mails."""
    cleaned = " ".join(_strip_control_chars(display_name or "").split())
    if not cleaned:
        cleaned = fallback
    return cleaned[:DISPLAY_NAME_MAX_LENGTH]


def validate_email(email: str) -> str:
    """Validação de forma, não de existência. Deliberadamente permissiva no
    que o RFC permite e restritiva no que quebra o resto do sistema."""
    cleaned = _strip_control_chars(email or "").strip()
    if not cleaned:
        raise InvalidEmailError("Informe um e-mail.")
    if len(cleaned) > EMAIL_MAX_LENGTH:
        raise InvalidEmailError("E-mail longo demais.")
    if not _EMAIL_PATTERN.match(cleaned):
        raise InvalidEmailError("Esse e-mail não parece válido.")
    return cleaned


def looks_like_email(identifier: str) -> bool:
    """Heurística só para telemetria/log — `authenticate()` NÃO depende disto
    para decidir o que procurar; ele procura pelos dois campos de uma vez."""
    return "@" in (identifier or "")


class UserRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # Criação
    # ------------------------------------------------------------------

    def create_user(
        self, *, username: str, display_name: str, password: str, email: str | None = None
    ) -> User:
        """Cria a conta. Username e e-mail precisam estar livres; se qualquer
        um falhar, **nada** é criado (item 37) — o INSERT é único e os índices
        UNIQUE do banco fazem a validação final de forma atômica."""
        cleaned_username = validate_username(username)
        normalized_username = normalize_username(cleaned_username)
        # Import local: `account_service` importa este módulo, então importar
        # de volta no topo fecharia um ciclo. A política de senha mora lá
        # porque é lá que ela é aplicada nas trocas.
        from services.account_service import validate_password

        cleaned_display = validate_display_name(display_name, fallback=cleaned_username)

        cleaned_email: str | None = None
        normalized_email: str | None = None
        if email:
            cleaned_email = validate_email(email)
            normalized_email = normalize_email(cleaned_email)

        # Depois de limpar identidade: a política v1.5.0 recusa senha derivada
        # do username/e-mail/nome, então precisa dos valores já normalizados.
        validate_password(
            password,
            username=cleaned_username,
            email=cleaned_email or "",
            display_name=cleaned_display,
        )

        password_hash = hash_password(password)
        user_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        try:
            self._conn.execute(
                "INSERT INTO users (id, username, normalized_username, display_name, password_hash, "
                "                   plan, created_at, email, normalized_email, email_verified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    user_id,
                    cleaned_username,
                    normalized_username,
                    cleaned_display,
                    password_hash,
                    Plan.FREE.value,
                    created_at,
                    cleaned_email,
                    normalized_email,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise self._identity_error(exc) from exc

        return User(
            id=user_id,
            username=cleaned_username,
            display_name=cleaned_display,
            plan=Plan.FREE,
            email=cleaned_email,
            email_verified=False,
        )

    @staticmethod
    def _identity_error(exc: sqlite3.IntegrityError) -> Exception:
        """Traduz a violação de índice único para o erro de domínio certo.
        Checa `normalized_email` antes de `email` porque o nome de um contém
        o do outro."""
        detail = str(exc).lower()
        if "normalized_email" in detail or "email" in detail:
            return EmailAlreadyRegisteredError("Não foi possível usar esse e-mail.")
        return UsernameAlreadyExistsError("Esse username já está em uso.")

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------

    def authenticate(self, *, identifier: str, password: str) -> User:
        """Valida credenciais aceitando **username OU e-mail** no mesmo campo
        (item 38). Levanta `AccountLockedError` em cooldown e
        `InvalidCredentialsError` para conta inexistente OU senha errada —
        mesma exceção, mesma mensagem, para não enumerar contas."""
        row = self._find_credentials_row(identifier)

        if row is None:
            # Hash descartável para o tempo de resposta não denunciar que a
            # conta não existe (a diferença seria de centenas de ms).
            verify_password(password, _DUMMY_HASH)
            raise InvalidCredentialsError("Usuário/e-mail ou senha incorretos.")

        remaining = self._lockout_remaining_seconds(row["lockout_until"])
        if remaining > 0:
            raise AccountLockedError(remaining)

        if not verify_password(password, row["password_hash"]):
            self._register_failed_attempt(row["id"], row["failed_login_attempts"])
            raise InvalidCredentialsError("Usuário/e-mail ou senha incorretos.")

        self._reset_failed_attempts(row["id"])
        return self._row_to_user(row)

    def _find_credentials_row(self, identifier: str) -> sqlite3.Row | None:
        """Uma query só para os dois campos: o banco decide se o identificador
        casa com username ou com e-mail. Fazer duas queries em sequência
        deixaria o caminho "existe como e-mail" mensuravelmente diferente do
        caminho "não existe"."""
        normalized = normalize_username(identifier)  # mesma normalização dos dois campos
        if not normalized:
            return None
        return self._conn.execute(
            "SELECT id, username, display_name, password_hash, plan, email, email_verified, "
            "       failed_login_attempts, lockout_until, totp_enabled "
            "FROM users WHERE normalized_username = ? OR normalized_email = ? LIMIT 1",
            (normalized, normalized),
        ).fetchone()

    def verify_password_for(self, user_id: str, password: str) -> bool:
        """Confere a senha de uma conta JÁ autenticada — base da reautenticação
        recente (item 45). Não mexe no contador de falhas de login: isso é
        confirmação de identidade dentro da sessão, não uma tentativa de
        entrar na conta, e contá-la permitiria travar a própria conta a
        partir de uma tela de configurações."""
        row = self._conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            verify_password(password, _DUMMY_HASH)
            return False
        return verify_password(password, row["password_hash"])

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    # SQL literal em cada método (e não uma constante interpolada com f-string):
    # a varredura estática de `tests/test_security_v1.py` proíbe query montada
    # por formatação dentro de `services/`. A lista de colunas seria segura
    # aqui, mas manter a regra sem exceção é o que faz a varredura valer
    # alguma coisa — a repetição é o preço.
    def get_user(self, user_id: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, username, display_name, plan, email, email_verified, totp_enabled, "
            "       created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def find_by_email(self, email: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, username, display_name, plan, email, email_verified, totp_enabled, "
            "       created_at FROM users WHERE normalized_email = ?",
            (normalize_email(email),),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def find_by_username(self, username: str) -> User | None:
        row = self._conn.execute(
            "SELECT id, username, display_name, plan, email, email_verified, totp_enabled, "
            "       created_at FROM users WHERE normalized_username = ?",
            (normalize_username(username),),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def has_any_user(self) -> bool:
        return self._conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def email_in_use(self, email: str, *, excluding_user_id: str | None = None) -> bool:
        """Checagem antecipada para dar erro cedo e bonito. NÃO é a garantia
        de unicidade — essa é o índice UNIQUE do banco, que também fecha a
        janela de corrida entre esta checagem e o UPDATE (item 36)."""
        normalized = normalize_email(email)
        if excluding_user_id:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE normalized_email = ? AND id <> ? LIMIT 1",
                (normalized, excluding_user_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE normalized_email = ? LIMIT 1", (normalized,)
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Perfil (v1.3)
    # ------------------------------------------------------------------

    def update_display_name(self, user_id: str, display_name: str) -> User | None:
        current = self.get_user(user_id)
        if current is None:
            return None
        cleaned = validate_display_name(display_name, fallback=current.username)
        self._conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?", (cleaned, user_id)
        )
        self._conn.commit()
        return self.get_user(user_id)

    def update_username(self, user_id: str, username: str) -> User | None:
        """Troca o username preservando `id` — tudo no sistema aponta para
        `user_id`, então chats/memória/sessões seguem intactos (item 40)."""
        cleaned = validate_username(username)
        normalized = normalize_username(cleaned)
        try:
            self._conn.execute(
                "UPDATE users SET username = ?, normalized_username = ? WHERE id = ?",
                (cleaned, normalized, user_id),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise UsernameAlreadyExistsError("Esse username já está em uso.") from exc
        return self.get_user(user_id)

    def set_email(self, user_id: str, email: str, *, verified: bool = False) -> User | None:
        """Define/troca o e-mail. Por padrão marca como NÃO verificado; o
        fluxo de troca de e-mail (`EmailChangeService`) passa `verified=True`
        porque lá o novo endereço só é gravado DEPOIS de confirmar o código
        enviado para ele."""
        cleaned = validate_email(email)
        normalized = normalize_email(cleaned)
        try:
            self._conn.execute(
                "UPDATE users SET email = ?, normalized_email = ?, email_verified = ? WHERE id = ?",
                (cleaned, normalized, 1 if verified else 0, user_id),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise EmailAlreadyRegisteredError("Não foi possível usar esse e-mail.") from exc
        return self.get_user(user_id)

    def mark_email_verified(self, user_id: str) -> None:
        self._conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
        self._conn.commit()

    def update_password(self, user_id: str, new_password: str) -> None:
        """Grava o novo hash. Quem valida a senha ATUAL e invalida as outras
        sessões é o serviço de conta (item 44) — o repositório só persiste."""
        if not new_password:
            raise ValueError("Senha não pode ser vazia.")
        self._conn.execute(
            "UPDATE users SET password_hash = ?, failed_login_attempts = 0, lockout_until = NULL "
            "WHERE id = ?",
            (hash_password(new_password), user_id),
        )
        self._conn.commit()

    def delete_user(self, user_id: str) -> bool:
        """Remove a conta. As tabelas dependentes têm `ON DELETE CASCADE` e o
        `PRAGMA foreign_keys = ON` é ligado em `connect()`, então sessões,
        conversas, mensagens, memórias, códigos e trocas pendentes vão junto.
        Ver `services/account_deletion.py` para o fluxo completo (que também
        cuida dos arquivos em disco)."""
        cursor = self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # 2FA — colunas TOTP (a orquestração fica em services/two_factor_service.py)
    # ------------------------------------------------------------------

    def set_totp_secret(self, user_id: str, protected_secret: str) -> None:
        """Guarda o segredo JÁ protegido (ver `services/secret_protection.py`)
        e deixa o 2FA DESLIGADO: só a confirmação do primeiro código válido
        ativa (item 47)."""
        self._conn.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled = 0, totp_confirmed_at = NULL, "
            "                 totp_failed_attempts = 0, totp_lockout_until = NULL WHERE id = ?",
            (protected_secret, user_id),
        )
        self._conn.commit()

    def get_totp_secret(self, user_id: str) -> str | None:
        """Devolve o valor PROTEGIDO como está no banco. Decifrar é
        responsabilidade de quem for usar, para o segredo em claro existir no
        menor escopo possível."""
        row = self._conn.execute(
            "SELECT totp_secret FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["totp_secret"] if row is not None else None

    def enable_totp(self, user_id: str) -> None:
        self._conn.execute(
            "UPDATE users SET totp_enabled = 1, totp_confirmed_at = ?, "
            "                 totp_failed_attempts = 0, totp_lockout_until = NULL WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self._conn.commit()

    def disable_totp(self, user_id: str) -> None:
        """Desliga E apaga o segredo (item 51) — deixar o segredo no banco
        depois de desativar seria guardar credencial sem propósito."""
        self._conn.execute(
            "UPDATE users SET totp_enabled = 0, totp_secret = NULL, totp_confirmed_at = NULL, "
            "                 totp_failed_attempts = 0, totp_lockout_until = NULL WHERE id = ?",
            (user_id,),
        )
        self._conn.commit()

    def is_totp_enabled(self, user_id: str) -> bool:
        row = self._conn.execute(
            "SELECT totp_enabled FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return bool(row["totp_enabled"]) if row is not None else False

    def totp_lockout_remaining(self, user_id: str) -> int:
        row = self._conn.execute(
            "SELECT totp_lockout_until FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._lockout_remaining_seconds(row["totp_lockout_until"]) if row else 0

    def register_totp_failure(self, user_id: str) -> int:
        """Backoff do segundo fator (item 53): mesma curva do login, nunca
        permanente. Devolve os segundos de bloqueio (0 se ainda não bloqueou).

        Sem isto, um código de 6 dígitos teria 1 em 1.000.000 por tentativa e
        milhares de tentativas por minuto — quebrável em horas."""
        row = self._conn.execute(
            "SELECT totp_failed_attempts FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        failures = ((row["totp_failed_attempts"] if row else 0) or 0) + 1
        seconds = 0
        lockout_until: str | None = None
        if failures >= _FAILURES_BEFORE_LOCKOUT:
            steps = failures - _FAILURES_BEFORE_LOCKOUT
            seconds = min(_BASE_LOCKOUT_SECONDS * (2**steps), _MAX_LOCKOUT_SECONDS)
            lockout_until = (
                datetime.now(timezone.utc) + timedelta(seconds=seconds)
            ).isoformat()
        self._conn.execute(
            "UPDATE users SET totp_failed_attempts = ?, totp_lockout_until = ? WHERE id = ?",
            (failures, lockout_until, user_id),
        )
        self._conn.commit()
        return seconds

    def reset_totp_failures(self, user_id: str) -> None:
        self._conn.execute(
            "UPDATE users SET totp_failed_attempts = 0, totp_lockout_until = NULL WHERE id = ?",
            (user_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Proteção contra força bruta (login)
    # ------------------------------------------------------------------

    @staticmethod
    def _lockout_remaining_seconds(lockout_until: str | None) -> int:
        if not lockout_until:
            return 0
        try:
            until = datetime.fromisoformat(lockout_until)
        except ValueError:
            return 0
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining + 0.999))  # arredonda para cima: nunca "0s" ainda bloqueado

    def _register_failed_attempt(self, user_id: str, current_failures: int) -> None:
        failures = (current_failures or 0) + 1
        lockout_until: str | None = None
        if failures >= _FAILURES_BEFORE_LOCKOUT:
            steps = failures - _FAILURES_BEFORE_LOCKOUT
            seconds = min(_BASE_LOCKOUT_SECONDS * (2**steps), _MAX_LOCKOUT_SECONDS)
            lockout_until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
            logger.warning("Conta em cooldown por %ss após %s tentativas falhas.", seconds, failures)
        self._conn.execute(
            "UPDATE users SET failed_login_attempts = ?, lockout_until = ? WHERE id = ?",
            (failures, lockout_until, user_id),
        )
        self._conn.commit()

    def _reset_failed_attempts(self, user_id: str) -> None:
        self._conn.execute(
            "UPDATE users SET failed_login_attempts = 0, lockout_until = NULL WHERE id = ?",
            (user_id,),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        keys = row.keys()

        def optional(name, default=None):
            return row[name] if name in keys else default

        created_raw = optional("created_at")
        created_at = None
        if created_raw:
            try:
                created_at = datetime.fromisoformat(created_raw)
            except ValueError:
                created_at = None

        fields = {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "plan": Plan(row["plan"]),
            "email": optional("email"),
            "email_verified": bool(optional("email_verified", 0)),
            "totp_enabled": bool(optional("totp_enabled", 0)),
        }
        if created_at is not None:
            fields["created_at"] = created_at
        return User(**fields)
