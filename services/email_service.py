"""`EmailService` — envio de e-mail, abstraído do provedor.

O JARVIS **nunca finge** ter enviado um e-mail: sem SMTP configurado,
`create_email_service()` devolve `UnavailableEmailService`, que reporta
`is_configured() == False` e levanta `EmailServiceNotConfiguredError` se
alguém tentar enviar mesmo assim (o `EmailVerificationService` traduz isso
para `AppErrorCode.EMAIL_SERVICE_NOT_CONFIGURED`, exibido claramente no HUD).

Nada aqui está amarrado a um provedor específico (Gmail e afins): é SMTP
genérico, configurado por variáveis de ambiente (`JARVIS_SMTP_*`). As
credenciais do remetente nunca aparecem em código, banco, QML, log ou Git.
"""

import asyncio
import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.message import EmailMessage
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class EmailServiceError(Exception):
    """Falha ao enviar — mensagem já segura para exibir (sem credencial)."""


class EmailServiceNotConfiguredError(EmailServiceError):
    """Nenhum SMTP configurado. Nunca tratado como "enviado com sucesso"."""


class EmailService(ABC):
    @abstractmethod
    def is_configured(self) -> bool:
        """Existe configuração suficiente para enviar de verdade?"""

    @abstractmethod
    async def send(self, *, to: str, subject: str, body: str) -> None:
        """Envia. Levanta `EmailServiceError` em falha — nunca silencia."""


class UnavailableEmailService(EmailService):
    """Placeholder honesto: sem SMTP configurado."""

    def is_configured(self) -> bool:
        return False

    async def send(self, *, to: str, subject: str, body: str) -> None:
        raise EmailServiceNotConfiguredError(
            "O serviço de e-mail não está configurado neste ambiente "
            "(defina JARVIS_SMTP_HOST e JARVIS_EMAIL_FROM)."
        )


class SmtpEmailService(EmailService):
    """SMTP genérico via `smtplib` da biblioteca padrão. O envio é
    bloqueante, então roda em executor para não travar o event loop."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        use_tls: bool = True,
        timeout_s: float = 30.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls
        self._timeout_s = timeout_s

    def is_configured(self) -> bool:
        return bool(self._host and self._sender)

    async def send(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._send_blocking, message)
        except EmailServiceError:
            raise
        except Exception as exc:
            # Nunca deixa a exceção crua do smtplib subir: ela pode conter
            # host/usuário em texto, e o HUD não deve ver detalhe de transporte.
            logger.exception("Falha ao enviar e-mail via SMTP.")
            raise EmailServiceError("Não foi possível enviar o e-mail. Verifique a configuração de SMTP.") from exc

    def _send_blocking(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout_s) as smtp:
            if self._use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(message)


def create_email_service(settings: "Settings") -> EmailService:
    """SMTP configurado -> `SmtpEmailService`; senão -> `UnavailableEmailService`.
    Nunca levanta: construir o serviço não conecta nada."""
    if not settings.has_smtp_config():
        return UnavailableEmailService()
    return SmtpEmailService(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        sender=settings.email_from,
        use_tls=settings.smtp_use_tls,
    )
