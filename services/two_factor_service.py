"""`TwoFactorService` — ativação, verificação e desativação do 2FA (TOTP).

Orquestra três peças que continuam com responsabilidade única:

    services/totp.py                 algoritmo (RFC 6238), sem estado
    services/secret_protection.py    cifra do segredo em repouso (DPAPI)
    services/user_repository.py      colunas TOTP + backoff
    services/recovery_code_repository.py   códigos de recuperação

Regras que moram aqui (itens 46-53):

- O 2FA **só fica ativo depois** de o usuário digitar um código correto
  (item 47). Entre gerar o segredo e confirmar, `totp_enabled` continua 0 —
  senão um erro no meio do cadastro trancaria a conta para fora.
- Ligar/desligar/regenerar exigem reautenticação recente (`ReauthGuard`), e
  desligar exige também um segundo fator válido (item 51).
- Verificação sofre backoff crescente (item 53), nunca bloqueio permanente.
- Um código de recuperação vale como segundo fator e é queimado no uso.
- O segredo **nunca** aparece em log, exceção, evento ou retorno para a IA.
  `start_enrollment()` é o ÚNICO ponto que devolve o segredo em claro, e só
  para a tela de configuração mostrar o QR e a chave.
"""

import logging
from dataclasses import dataclass, field

from app.models import AppError, AppErrorCode
from services import totp as totp_module
from services.reauth import ReauthGuard, SensitiveAction
from services.recovery_code_repository import RecoveryCodeRepository
from services.secret_protection import is_protection_available, protect, unprotect
from services.user_repository import UserRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TwoFactorEnrollment:
    """Dados de tela para ativar o 2FA. Existe só em RAM, entre
    `start_enrollment()` e `confirm_enrollment()` — nunca é persistido nem
    emitido como evento."""

    secret: str
    provisioning_uri: str
    qr_matrix: list[list[bool]] = field(default_factory=list)
    secret_protected: bool = True

    @property
    def formatted_secret(self) -> str:
        """Chave em grupos de 4 para o usuário conseguir digitar (fallback
        textual do item 47)."""
        raw = self.secret
        return " ".join(raw[index : index + 4] for index in range(0, len(raw), 4))


@dataclass(frozen=True)
class TwoFactorStatus:
    enabled: bool
    enrollment_pending: bool
    recovery_codes_remaining: int
    secret_protected: bool
    lockout_seconds: int = 0


class TwoFactorService:
    def __init__(
        self,
        users: UserRepository,
        recovery_codes: RecoveryCodeRepository,
        *,
        reauth: ReauthGuard,
        issuer: str = "JARVIS",
    ) -> None:
        self._users = users
        self._recovery = recovery_codes
        self._reauth = reauth
        self._issuer = issuer

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def status(self, user_id: str) -> TwoFactorStatus:
        stored = self._users.get_totp_secret(user_id)
        enabled = self._users.is_totp_enabled(user_id)
        return TwoFactorStatus(
            enabled=enabled,
            enrollment_pending=bool(stored) and not enabled,
            recovery_codes_remaining=self._recovery.remaining(user_id),
            secret_protected=is_protection_available(),
            lockout_seconds=self._users.totp_lockout_remaining(user_id),
        )

    # ------------------------------------------------------------------
    # Ativação
    # ------------------------------------------------------------------

    def start_enrollment(
        self, *, user_id: str, account_name: str
    ) -> tuple[TwoFactorEnrollment | None, AppError | None]:
        """Gera um segredo novo e devolve o material para o QR.

        Chamar de novo antes de confirmar simplesmente descarta o segredo
        anterior — o usuário pode reabrir a tela sem ficar preso a um QR que
        ele não escaneou."""
        if not self._reauth.require(SensitiveAction.ENABLE_TWO_FACTOR):
            return None, AppError(
                AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha para ativar a verificação em duas etapas."
            )
        if self._users.is_totp_enabled(user_id):
            return None, AppError(
                AppErrorCode.TWO_FACTOR_ALREADY_ENABLED, "A verificação em duas etapas já está ativa."
            )

        secret = totp_module.generate_secret()
        self._users.set_totp_secret(user_id, protect(secret))

        uri = totp_module.provisioning_uri(
            secret=secret, account_name=account_name, issuer=self._issuer
        )
        # Nunca logar `secret` nem `uri` (a URI CONTÉM o segredo).
        logger.info("Ativação de 2FA iniciada para uma conta local.")
        return (
            TwoFactorEnrollment(
                secret=secret,
                provisioning_uri=uri,
                qr_matrix=totp_module.qr_matrix(uri),
                secret_protected=is_protection_available(),
            ),
            None,
        )

    def confirm_enrollment(
        self, *, user_id: str, code: str
    ) -> tuple[list[str] | None, AppError | None]:
        """Confirma o primeiro código e ATIVA. Devolve os códigos de
        recuperação em plaintext — única vez que existem (item 49)."""
        if not self._reauth.require(SensitiveAction.ENABLE_TWO_FACTOR):
            return None, AppError(AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha novamente.")
        if self._users.is_totp_enabled(user_id):
            return None, AppError(
                AppErrorCode.TWO_FACTOR_ALREADY_ENABLED, "A verificação em duas etapas já está ativa."
            )

        secret = self._current_secret(user_id)
        if secret is None:
            return None, AppError(
                AppErrorCode.TWO_FACTOR_NOT_ENABLED,
                "Nenhuma ativação em andamento. Comece de novo.",
            )

        error = self._check_code(user_id=user_id, secret=secret, code=code)
        if error is not None:
            return None, error

        self._users.enable_totp(user_id)
        codes = self._recovery.generate(user_id)
        logger.info("2FA ativado para uma conta local.")
        return codes, None

    # ------------------------------------------------------------------
    # Verificação (login e operações sensíveis)
    # ------------------------------------------------------------------

    def verify(self, *, user_id: str, code: str, allow_recovery: bool = True) -> AppError | None:
        """`None` = segundo fator aceito.

        Aceita TOTP ou código de recuperação — o usuário digita no mesmo
        campo, e tentar os dois evita uma escolha de UI que não agrega."""
        lockout = self._users.totp_lockout_remaining(user_id)
        if lockout > 0:
            return AppError(
                AppErrorCode.TWO_FACTOR_RATE_LIMITED,
                f"Tentativas demais. Tente novamente em {lockout}s.",
            )

        secret = self._current_secret(user_id)
        if secret is None:
            return AppError(
                AppErrorCode.TWO_FACTOR_NOT_ENABLED, "A verificação em duas etapas não está ativa."
            )

        cleaned = (code or "").strip()
        if totp_module.verify(secret, cleaned):
            self._users.reset_totp_failures(user_id)
            return None

        if allow_recovery and self._recovery.consume(user_id, cleaned):
            self._users.reset_totp_failures(user_id)
            return None

        seconds = self._users.register_totp_failure(user_id)
        if seconds > 0:
            return AppError(
                AppErrorCode.TWO_FACTOR_RATE_LIMITED,
                f"Tentativas demais. Tente novamente em {seconds}s.",
            )
        return AppError(AppErrorCode.TWO_FACTOR_INVALID, "Código incorreto.")

    def _check_code(self, *, user_id: str, secret: str, code: str) -> AppError | None:
        """Confirmação de ativação: só TOTP. Aceitar código de recuperação
        aqui não faria sentido — eles ainda nem foram gerados."""
        lockout = self._users.totp_lockout_remaining(user_id)
        if lockout > 0:
            return AppError(
                AppErrorCode.TWO_FACTOR_RATE_LIMITED,
                f"Tentativas demais. Tente novamente em {lockout}s.",
            )
        if totp_module.verify(secret, (code or "").strip()):
            self._users.reset_totp_failures(user_id)
            return None
        seconds = self._users.register_totp_failure(user_id)
        if seconds > 0:
            return AppError(
                AppErrorCode.TWO_FACTOR_RATE_LIMITED,
                f"Tentativas demais. Tente novamente em {seconds}s.",
            )
        return AppError(AppErrorCode.TWO_FACTOR_INVALID, "Código incorreto.")

    # ------------------------------------------------------------------
    # Códigos de recuperação e desativação
    # ------------------------------------------------------------------

    def regenerate_recovery_codes(
        self, *, user_id: str, code: str
    ) -> tuple[list[str] | None, AppError | None]:
        """Item 50: exige senha recente **e** segundo fator; os antigos são
        invalidados no mesmo movimento (`RecoveryCodeRepository.generate`
        apaga tudo antes de inserir)."""
        if not self._reauth.require(SensitiveAction.REGENERATE_RECOVERY_CODES):
            return None, AppError(AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha.")
        if not self._users.is_totp_enabled(user_id):
            return None, AppError(
                AppErrorCode.TWO_FACTOR_NOT_ENABLED, "A verificação em duas etapas não está ativa."
            )
        # `allow_recovery=False`: usar um código de recuperação para gerar
        # códigos novos deixaria um código vazado se auto-renovar para sempre.
        error = self.verify(user_id=user_id, code=code, allow_recovery=False)
        if error is not None:
            return None, error
        return self._recovery.generate(user_id), None

    def disable(self, *, user_id: str, code: str) -> AppError | None:
        """Item 51: senha recente + segundo fator. Revoga o segredo E os
        códigos de recuperação."""
        if not self._reauth.require(SensitiveAction.DISABLE_TWO_FACTOR):
            return AppError(AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha.")
        if not self._users.is_totp_enabled(user_id):
            return AppError(
                AppErrorCode.TWO_FACTOR_NOT_ENABLED, "A verificação em duas etapas não está ativa."
            )
        error = self.verify(user_id=user_id, code=code)
        if error is not None:
            return error
        self._users.disable_totp(user_id)
        self._recovery.clear(user_id)
        logger.info("2FA desativado para uma conta local.")
        return None

    def cancel_enrollment(self, user_id: str) -> None:
        """Descarta um segredo gerado mas nunca confirmado. Só age quando o
        2FA NÃO está ativo — senão isto viraria um caminho para desligar o
        2FA sem segundo fator."""
        if not self._users.is_totp_enabled(user_id):
            self._users.disable_totp(user_id)

    def _current_secret(self, user_id: str) -> str | None:
        stored = self._users.get_totp_secret(user_id)
        return unprotect(stored) if stored else None
