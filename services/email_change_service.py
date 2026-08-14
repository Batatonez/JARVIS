"""Troca de e-mail em duas fases (v1.3, itens 41-43).

Trocar o e-mail é a operação mais sensível de uma conta depois da senha: o
e-mail é o caminho de recuperação. Por isso o fluxo é:

    reautenticação recente (senha)
    -> novo e-mail validado e confirmado como livre
    -> código enviado ao NOVO endereço
    -> código conferido
    -> e-mail trocado (já verificado)
    -> aviso ao endereço ANTIGO

O e-mail atual **continua valendo** até a confirmação: a troca só grava em
`users` no último passo. Um pedido abandonado no meio não muda nada.

As regras de tempo são as mesmas da verificação de conta (v1.0), de
propósito — duas políticas diferentes para "digitar um código de 6 dígitos"
seria confuso e daria margem a bug: expira em 5 minutos, reenvio a cada 60
segundos, código novo invalida o anterior, no máximo 5 tentativas.

Repositório e serviço moram no mesmo módulo aqui porque a tabela
`pending_email_changes` tem exatamente um consumidor — separar criaria um
arquivo que só existiria para ser importado por outro.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import AppError, AppErrorCode
from services.email_service import EmailService, EmailServiceError, EmailServiceNotConfiguredError
from services.email_verification_service import generate_code, mask_email
from services.password_hashing import hash_password, verify_password
from services.reauth import ReauthGuard, SensitiveAction
from services.user_repository import (
    EmailAlreadyRegisteredError,
    InvalidEmailError,
    UserRepository,
    normalize_email,
    validate_email,
)

logger = logging.getLogger(__name__)

_EXPIRY_MINUTES = 5
_RESEND_SECONDS = 60
_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class PendingEmailChange:
    """Estado de uma troca em andamento. Nunca carrega o código — só o
    suficiente para o HUD mostrar os dois contadores."""

    id: str
    new_email: str
    expires_at: datetime
    resend_available_at: datetime
    attempts: int

    def seconds_until_expiry(self) -> int:
        return max(0, int((self.expires_at - datetime.now(timezone.utc)).total_seconds()))

    def seconds_until_resend(self) -> int:
        return max(0, int((self.resend_available_at - datetime.now(timezone.utc)).total_seconds()))

    @property
    def masked_email(self) -> str:
        return mask_email(self.new_email)


class PendingEmailChangeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def create(self, *, user_id: str, new_email: str, code: str) -> PendingEmailChange:
        """Emitir um pedido novo invalida o anterior — nunca existem dois
        códigos válidos ao mesmo tempo para a mesma conta."""
        self.consume_active(user_id)
        now = datetime.now(timezone.utc)
        pending = PendingEmailChange(
            id=str(uuid4()),
            new_email=new_email,
            expires_at=now + timedelta(minutes=_EXPIRY_MINUTES),
            resend_available_at=now + timedelta(seconds=_RESEND_SECONDS),
            attempts=0,
        )
        self._conn.execute(
            "INSERT INTO pending_email_changes (id, user_id, new_email, normalized_new_email, "
            "    code_hash, created_at, expires_at, resend_available_at, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                pending.id,
                user_id,
                new_email,
                normalize_email(new_email),
                hash_password(code),
                now.isoformat(),
                pending.expires_at.isoformat(),
                pending.resend_available_at.isoformat(),
            ),
        )
        self._conn.commit()
        return pending

    def active(self, user_id: str) -> PendingEmailChange | None:
        row = self._conn.execute(
            "SELECT id, new_email, expires_at, resend_available_at, attempts "
            "FROM pending_email_changes WHERE user_id = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return PendingEmailChange(
            id=row["id"],
            new_email=row["new_email"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
            resend_available_at=datetime.fromisoformat(row["resend_available_at"]),
            attempts=row["attempts"],
        )

    def verify_code(self, *, user_id: str, code: str) -> tuple[bool, PendingEmailChange | None]:
        pending = self.active(user_id)
        if pending is None:
            return False, None
        if pending.attempts >= _MAX_ATTEMPTS:
            return False, pending

        row = self._conn.execute(
            "SELECT code_hash FROM pending_email_changes WHERE id = ?", (pending.id,)
        ).fetchone()
        if row is None:
            return False, None

        if verify_password((code or "").strip(), row["code_hash"]):
            return True, pending

        self._conn.execute(
            "UPDATE pending_email_changes SET attempts = attempts + 1 WHERE id = ?", (pending.id,)
        )
        self._conn.commit()
        return False, self.active(user_id)

    def consume_active(self, user_id: str) -> None:
        self._conn.execute(
            "UPDATE pending_email_changes SET consumed_at = ? "
            "WHERE user_id = ? AND consumed_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self._conn.commit()


@dataclass(frozen=True)
class EmailChangeRequestResult:
    sent: bool
    pending: PendingEmailChange | None = None
    error: AppError | None = None


class EmailChangeService:
    def __init__(
        self,
        repository: PendingEmailChangeRepository,
        users: UserRepository,
        email_service: EmailService,
        *,
        reauth: ReauthGuard,
        app_name: str = "JARVIS",
    ) -> None:
        self._repository = repository
        self._users = users
        self._email = email_service
        self._reauth = reauth
        self._app_name = app_name

    def active_request(self, user_id: str) -> PendingEmailChange | None:
        return self._repository.active(user_id)

    async def request_change(
        self, *, user_id: str, new_email: str, force: bool = False
    ) -> EmailChangeRequestResult:
        if not self._reauth.require(SensitiveAction.CHANGE_EMAIL):
            return EmailChangeRequestResult(
                sent=False,
                error=AppError(
                    AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha para trocar o e-mail."
                ),
            )

        try:
            cleaned = validate_email(new_email)
        except InvalidEmailError as exc:
            return EmailChangeRequestResult(
                sent=False, error=AppError(AppErrorCode.INVALID_EMAIL, str(exc))
            )

        # Item 42: e-mail ocupado devolve o erro estruturado, sem dizer de
        # quem é. A garantia final continua sendo o índice UNIQUE, aplicado
        # no `set_email` do último passo.
        if self._users.email_in_use(cleaned, excluding_user_id=user_id):
            return EmailChangeRequestResult(
                sent=False,
                error=AppError(
                    AppErrorCode.EMAIL_ALREADY_IN_USE, "Não foi possível usar esse e-mail."
                ),
            )

        if not self._email.is_configured():
            return EmailChangeRequestResult(
                sent=False,
                error=AppError(
                    AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED,
                    "O envio de e-mail não está configurado neste ambiente. "
                    "Configure JARVIS_SMTP_HOST e JARVIS_EMAIL_FROM para trocar o e-mail.",
                ),
            )

        existing = self._repository.active(user_id)
        if not force and existing is not None:
            remaining = existing.seconds_until_resend()
            if remaining > 0:
                return EmailChangeRequestResult(
                    sent=False,
                    pending=existing,
                    error=AppError(
                        AppErrorCode.VERIFICATION_RESEND_TOO_SOON,
                        f"Aguarde {remaining}s para reenviar o código.",
                    ),
                )

        code = generate_code()
        pending = self._repository.create(user_id=user_id, new_email=cleaned, code=code)

        try:
            await self._email.send(
                to=cleaned,
                subject=f"{self._app_name} — confirme seu novo e-mail",
                body=(
                    f"Use este código para confirmar seu novo e-mail no {self._app_name}: {code}\n\n"
                    "Ele expira em 5 minutos e só pode ser usado uma vez.\n"
                    "Seu e-mail atual continua valendo até você confirmar.\n"
                    "Se você não pediu esta troca, ignore este e-mail."
                ),
            )
        except (EmailServiceNotConfiguredError, EmailServiceError) as exc:
            self._repository.consume_active(user_id)
            logger.warning("Falha ao enviar código de troca de e-mail: %s", exc)
            return EmailChangeRequestResult(
                sent=False,
                error=AppError(AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED, str(exc)),
            )

        logger.info("Código de troca de e-mail enviado para %s.", mask_email(cleaned))
        return EmailChangeRequestResult(sent=True, pending=pending)

    async def confirm_change(self, *, user_id: str, code: str) -> AppError | None:
        """`None` = e-mail trocado. O novo endereço entra JÁ verificado: o
        código foi enviado para ele e conferido, que é exatamente a prova que
        a verificação de e-mail busca."""
        if not self._reauth.require(SensitiveAction.CHANGE_EMAIL):
            return AppError(AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha novamente.")

        pending = self._repository.active(user_id)
        if pending is None:
            return AppError(
                AppErrorCode.VERIFICATION_CODE_INVALID,
                "Nenhuma troca de e-mail em andamento. Comece de novo.",
            )
        if pending.seconds_until_expiry() <= 0:
            self._repository.consume_active(user_id)
            return AppError(
                AppErrorCode.VERIFICATION_CODE_EXPIRED, "O código expirou. Peça um novo código."
            )

        matched, updated = self._repository.verify_code(user_id=user_id, code=code)
        if not matched:
            if updated is not None and updated.attempts >= _MAX_ATTEMPTS:
                return AppError(
                    AppErrorCode.VERIFICATION_TOO_MANY_ATTEMPTS,
                    "Tentativas demais. Peça um novo código.",
                )
            return AppError(AppErrorCode.VERIFICATION_CODE_INVALID, "Código incorreto.")

        previous = self._users.get_user(user_id)
        previous_email = previous.email if previous else None

        try:
            self._users.set_email(user_id, pending.new_email, verified=True)
        except EmailAlreadyRegisteredError:
            # Corrida: alguém registrou este e-mail entre o pedido e a
            # confirmação. O índice UNIQUE pegou — a conta fica intacta.
            self._repository.consume_active(user_id)
            return AppError(
                AppErrorCode.EMAIL_ALREADY_IN_USE, "Não foi possível usar esse e-mail."
            )

        self._repository.consume_active(user_id)
        await self._notify_previous_email(previous_email, pending.new_email)
        logger.info("E-mail da conta trocado para %s.", mask_email(pending.new_email))
        return None

    async def _notify_previous_email(self, previous_email: str | None, new_email: str) -> None:
        """Item 43: avisa o endereço antigo. A falha deste aviso **não**
        desfaz a troca já concluída — o e-mail novo já foi provado, e reverter
        por causa de um informativo deixaria a conta num estado pior."""
        if not previous_email or not self._email.is_configured():
            return
        try:
            await self._email.send(
                to=previous_email,
                subject=f"{self._app_name} — o e-mail da sua conta foi alterado",
                body=(
                    f"O e-mail da sua conta {self._app_name} foi alterado para "
                    f"{mask_email(new_email)}.\n\n"
                    "Se não foi você, entre na sua conta e troque a senha imediatamente."
                ),
            )
        except Exception as exc:  # noqa: BLE001 — informativo nunca derruba a operação
            logger.warning("Não foi possível avisar o e-mail anterior sobre a troca: %s", exc)
