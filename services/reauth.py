"""Reautenticação recente — mecanismo ÚNICO e centralizado (item 45).

Operações sensíveis (trocar senha, trocar e-mail, ligar/desligar 2FA,
regenerar códigos de recuperação, revogar sessões, apagar a conta) não podem
depender só de "a sessão está aberta": um computador destravado por alguns
minutos viraria uma tomada de conta.

Este módulo existe para essa regra morar em UM lugar. Cada tela chama
`require()` antes de agir; nenhuma reimplementa a política, e mudar a janela
de validade é uma linha só.

Não persiste nada: a confirmação vale para a sessão em RAM. Fechar o JARVIS
zera — que é o comportamento conservador correto.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# 5 minutos: tempo confortável para o usuário fazer uma sequência de ajustes
# em Account Settings sem redigitar a senha a cada campo, e curto o bastante
# para uma máquina destravada não virar um cheque em branco.
DEFAULT_WINDOW_SECONDS = 5 * 60


class SensitiveAction(Enum):
    """Enumerado em vez de string livre para não haver ação sensível "nova"
    entrando sem passar por aqui."""

    CHANGE_PASSWORD = "change_password"
    CHANGE_EMAIL = "change_email"
    CHANGE_USERNAME = "change_username"
    ENABLE_TWO_FACTOR = "enable_two_factor"
    DISABLE_TWO_FACTOR = "disable_two_factor"
    REGENERATE_RECOVERY_CODES = "regenerate_recovery_codes"
    REVOKE_SESSIONS = "revoke_sessions"
    DELETE_ACCOUNT = "delete_account"


@dataclass(frozen=True)
class ReauthState:
    """Só o que a UI precisa saber para decidir se pede a senha."""

    valid: bool
    remaining_seconds: int


class ReauthGuard:
    """Guarda o instante da última confirmação de senha da sessão atual.

    `time_source` é injetável para os testes usarem tempo mockado (item 68)
    sem `sleep` — nunca para produção passar um relógio de fora.
    """

    def __init__(self, *, window_seconds: int = DEFAULT_WINDOW_SECONDS, time_source=time.monotonic) -> None:
        self._window = window_seconds
        self._now = time_source
        self._confirmed_at: float | None = None

    def confirm(self) -> None:
        """Chamado DEPOIS de a senha ter sido validada pelo repositório —
        este módulo nunca vê senha, só registra que a validação aconteceu."""
        self._confirmed_at = self._now()

    def invalidate(self) -> None:
        """Zera a confirmação. Chamado no logout e depois de qualquer operação
        que mude as credenciais, para a janela não sobreviver a uma troca de
        senha."""
        self._confirmed_at = None

    def state(self) -> ReauthState:
        if self._confirmed_at is None:
            return ReauthState(valid=False, remaining_seconds=0)
        elapsed = self._now() - self._confirmed_at
        remaining = self._window - elapsed
        if remaining <= 0:
            return ReauthState(valid=False, remaining_seconds=0)
        return ReauthState(valid=True, remaining_seconds=int(remaining))

    def is_valid(self) -> bool:
        return self.state().valid

    def require(self, action: SensitiveAction) -> bool:
        """`True` se a ação pode prosseguir. Loga a NEGAÇÃO (útil para
        auditoria) e nunca o sucesso silencioso de uma ação sensível sem
        confirmação."""
        if self.is_valid():
            return True
        logger.info("Ação sensível '%s' exige reautenticação recente.", action.value)
        return False
