"""`EmailVerificationService` — orquestra geração, envio e conferência do
código de verificação de e-mail.

Todas as regras de tempo são validadas **aqui**, contra o relógio real e o
que está gravado no banco (`expires_at`/`resend_available_at`) — o frontend
nunca é autoridade sobre isso. Fechar e reabrir o JARVIS não "reinicia" o
cooldown nem estende a validade: os dois contadores do HUD são derivados dos
timestamps persistidos.

Regras (v1.0): código expira em 5 minutos, reenvio liberado após 60
segundos, uso único, no máximo 5 tentativas, e emitir um novo invalida o
anterior (ver `services/email_verification_repository.py`).
"""

import logging
import secrets
from dataclasses import dataclass

from app.models import AppError, AppErrorCode
from services.email_service import EmailService, EmailServiceError, EmailServiceNotConfiguredError
from services.email_verification_repository import (
    EmailVerificationRepository,
    VerificationChallenge,
)

logger = logging.getLogger(__name__)

_CODE_DIGITS = 6


def generate_code() -> str:
    """6 dígitos via `secrets` (nunca `random`). Zero-padded — `042315` é um
    código válido, e cortar o zero à esquerda reduziria o espaço."""
    return f"{secrets.randbelow(10**_CODE_DIGITS):0{_CODE_DIGITS}d}"


def mask_email(email: str) -> str:
    """`davi@example.com` -> `d***@example.com`. A tela de verificação não
    precisa mostrar o endereço inteiro para o usuário reconhecê-lo."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    visible = local[:1] if local else ""
    return f"{visible}***@{domain}"


@dataclass(frozen=True)
class VerificationRequestResult:
    """Resultado de pedir/reenviar um código. `challenge` é `None` quando
    nada foi criado (erro) — os contadores do HUD vêm dele."""

    sent: bool
    challenge: VerificationChallenge | None = None
    error: AppError | None = None


class EmailVerificationService:
    def __init__(
        self,
        repository: EmailVerificationRepository,
        email_service: EmailService,
        *,
        app_name: str = "JARVIS",
    ) -> None:
        self._repository = repository
        self._email = email_service
        self._app_name = app_name

    @property
    def email_configured(self) -> bool:
        return self._email.is_configured()

    def active_challenge(self, user_id: str) -> VerificationChallenge | None:
        return self._repository.active_challenge(user_id)

    async def request_code(self, *, user_id: str, email: str, force: bool = False) -> VerificationRequestResult:
        """Emite e envia um código. `force=False` respeita o cooldown de
        reenvio (60s) de um desafio ativo — é o caminho do botão "reenviar".
        `force=True` é para o primeiro envio logo após o cadastro."""
        if not self._email.is_configured():
            return VerificationRequestResult(
                sent=False,
                error=AppError(
                    AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED,
                    "O envio de e-mail não está configurado neste ambiente. "
                    "Configure JARVIS_SMTP_HOST e JARVIS_EMAIL_FROM para verificar a conta.",
                ),
            )

        existing = self._repository.active_challenge(user_id)
        if not force and existing is not None:
            remaining = existing.seconds_until_resend()
            if remaining > 0:
                return VerificationRequestResult(
                    sent=False,
                    challenge=existing,
                    error=AppError(
                        AppErrorCode.VERIFICATION_RESEND_TOO_SOON,
                        f"Aguarde {remaining}s para reenviar o código.",
                    ),
                )

        code = generate_code()
        challenge = self._repository.create_challenge(user_id=user_id, email=email, code=code)

        try:
            await self._email.send(
                to=email,
                subject=f"{self._app_name} — código de verificação",
                body=(
                    f"Seu código de verificação do {self._app_name} é: {code}\n\n"
                    "Ele expira em 5 minutos e só pode ser usado uma vez.\n"
                    "Se você não pediu este código, ignore este e-mail."
                ),
            )
        except EmailServiceNotConfiguredError:
            self._repository.consume_active(user_id)
            return VerificationRequestResult(
                sent=False,
                error=AppError(
                    AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED,
                    "O envio de e-mail não está configurado neste ambiente.",
                ),
            )
        except EmailServiceError as exc:
            # O código já foi gravado; invalida para não deixar um desafio
            # ativo que o usuário nunca recebeu.
            self._repository.consume_active(user_id)
            logger.warning("Falha ao enviar código de verificação: %s", exc)
            return VerificationRequestResult(
                sent=False,
                error=AppError(AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED, str(exc)),
            )

        # Nunca logamos o código em si — só o fato do envio.
        logger.info("Código de verificação enviado para %s.", mask_email(email))
        return VerificationRequestResult(sent=True, challenge=challenge)

    def verify(self, *, user_id: str, code: str) -> AppError | None:
        """`None` = verificado com sucesso. Caso contrário, o erro
        estruturado correspondente."""
        code = (code or "").strip()
        challenge = self._repository.active_challenge(user_id)
        if challenge is None:
            return AppError(
                AppErrorCode.VERIFICATION_CODE_INVALID,
                "Nenhum código ativo. Peça um novo código.",
            )
        if challenge.seconds_until_expiry() <= 0:
            self._repository.consume_active(user_id)
            return AppError(
                AppErrorCode.VERIFICATION_CODE_EXPIRED, "O código expirou. Peça um novo código."
            )

        matched, updated = self._repository.verify_code(user_id=user_id, code=code)
        if matched:
            return None

        if updated is not None and updated.attempts >= 5:
            return AppError(
                AppErrorCode.VERIFICATION_TOO_MANY_ATTEMPTS,
                "Tentativas demais. Peça um novo código.",
            )
        return AppError(AppErrorCode.VERIFICATION_CODE_INVALID, "Código incorreto.")
