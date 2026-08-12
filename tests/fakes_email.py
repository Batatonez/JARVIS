"""Fakes de e-mail — nenhum teste automático pode enviar e-mail de verdade
(item 81 do escopo v1.0). `FakeEmailService` guarda as mensagens em memória
para os testes inspecionarem o que *seria* enviado."""

import re
from dataclasses import dataclass

from services.email_service import EmailService, EmailServiceError


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str


class FakeEmailService(EmailService):
    """`configured=False` simula um ambiente sem SMTP; `fail=True` simula
    uma falha de envio (SMTP recusando, rede caindo)."""

    def __init__(self, *, configured: bool = True, fail: bool = False) -> None:
        self._configured = configured
        self._fail = fail
        self.sent: list[SentEmail] = []

    def is_configured(self) -> bool:
        return self._configured

    async def send(self, *, to: str, subject: str, body: str) -> None:
        if self._fail:
            raise EmailServiceError("Falha simulada de envio de e-mail.")
        self.sent.append(SentEmail(to=to, subject=subject, body=body))

    # --- utilidades de teste ---

    @property
    def last_code(self) -> str | None:
        """Extrai o código de 6 dígitos do corpo do último e-mail. Só existe
        porque o código nunca é devolvido pela API (ele só sai por e-mail) —
        é assim que um teste "recebe" o código."""
        if not self.sent:
            return None
        match = re.search(r"\b(\d{6})\b", self.sent[-1].body)
        return match.group(1) if match else None
