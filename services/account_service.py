"""`AccountService` — operações de "Account Settings" que não são 2FA nem
troca de e-mail (v1.3, itens 30-33, 40, 44, 45, 55).

Fica entre o `AccountManager` (fachada que o HUD enxerga) e os repositórios.
Existe para que a POLÍTICA more num lugar só:

- o que exige reautenticação recente (`ReauthGuard`);
- o que invalida sessões;
- qual erro estruturado cada falha produz.

Nada aqui confia no frontend: `user_id` sempre vem do `AccountManager` (a
sessão autenticada), nunca de um parâmetro que o QML possa escolher (item
60). Um `AppError` de retorno é sempre uma mensagem já apresentável — nunca
um traceback nem detalhe de banco.
"""

import logging

from app.models import AppError, AppErrorCode, User
from services.reauth import ReauthGuard, SensitiveAction
from services.session_repository import SessionRepository
from services.user_repository import (
    InvalidUsernameError,
    UserRepository,
    UsernameAlreadyExistsError,
)

logger = logging.getLogger(__name__)

# Mínimo de 8 caracteres — o piso recomendado pelo OWASP para senha de
# usuário. Sem teto artificial baixo e sem exigência de "1 maiúscula, 1
# símbolo": regras de composição empurram o usuário para "Senha1!" e não
# aumentam a entropia real.
PASSWORD_MIN_LENGTH = 8


class InvalidPasswordError(ValueError):
    """Senha nova fora da política."""


def validate_password(password: str) -> str:
    if not password:
        raise InvalidPasswordError("Informe uma senha.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise InvalidPasswordError(
            f"A senha precisa ter pelo menos {PASSWORD_MIN_LENGTH} caracteres."
        )
    return password


class AccountService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        *,
        reauth: ReauthGuard,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._reauth = reauth

    # ------------------------------------------------------------------
    # Reautenticação
    # ------------------------------------------------------------------

    def confirm_password(self, *, user_id: str, password: str) -> AppError | None:
        """Porta de entrada de toda operação sensível (item 45). Sucesso abre
        a janela de reautenticação; falha não abre nada."""
        if not self._users.verify_password_for(user_id, password):
            logger.info("Confirmação de senha recusada em operação sensível.")
            return AppError(AppErrorCode.INVALID_PASSWORD, "Senha incorreta.")
        self._reauth.confirm()
        return None

    # ------------------------------------------------------------------
    # Perfil
    # ------------------------------------------------------------------

    def change_display_name(self, *, user_id: str, display_name: str) -> tuple[User | None, AppError | None]:
        """Nome de exibição é puramente visual (item 32) — não é identificador
        e não decide nada de segurança, então **não** exige reautenticação."""
        user = self._users.update_display_name(user_id, display_name)
        if user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Conta não encontrada.")
        return user, None

    def change_username(self, *, user_id: str, username: str) -> tuple[User | None, AppError | None]:
        """Username é identificador de login: exige senha recente. O `id` da
        conta não muda, então chats, memória e sessões continuam ligados
        (item 40)."""
        if not self._reauth.require(SensitiveAction.CHANGE_USERNAME):
            return None, AppError(
                AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha para trocar o username."
            )
        try:
            user = self._users.update_username(user_id, username)
        except InvalidUsernameError as exc:
            return None, AppError(AppErrorCode.INVALID_USERNAME, str(exc))
        except UsernameAlreadyExistsError as exc:
            return None, AppError(AppErrorCode.USERNAME_ALREADY_IN_USE, str(exc))
        if user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Conta não encontrada.")
        logger.info("Username alterado para uma conta local.")
        return user, None

    # ------------------------------------------------------------------
    # Senha
    # ------------------------------------------------------------------

    def change_password(
        self,
        *,
        user_id: str,
        current_password: str,
        new_password: str,
        confirm_password: str,
        current_token: str | None = None,
    ) -> tuple[int, AppError | None]:
        """Troca a senha e revoga as OUTRAS sessões (item 44). Devolve
        `(sessões_revogadas, erro)`.

        A senha atual é conferida aqui mesmo, além da janela de
        reautenticação: trocar senha é a operação que dá controle permanente
        da conta, e é razoável exigir a senha no próprio formulário."""
        if not self._users.verify_password_for(user_id, current_password):
            return 0, AppError(AppErrorCode.INVALID_PASSWORD, "Senha atual incorreta.")
        if new_password != confirm_password:
            return 0, AppError(
                AppErrorCode.CONFIRMATION_MISMATCH, "A confirmação não confere com a nova senha."
            )
        try:
            validate_password(new_password)
        except InvalidPasswordError as exc:
            return 0, AppError(AppErrorCode.INVALID_PASSWORD, str(exc))

        self._users.update_password(user_id, new_password)

        # A sessão atual sobrevive (o usuário não é deslogado do próprio
        # aparelho); todas as outras caem. Sem `current_token` — caminho de
        # CLI/teste — revoga tudo, que é o lado seguro.
        if current_token:
            revoked = self._sessions.delete_others_for_user(user_id, keep_token=current_token)
        else:
            revoked = self._sessions.delete_all_for_user(user_id)

        # A janela de reautenticação morre junto com a senha antiga.
        self._reauth.invalidate()
        logger.info("Senha alterada; %s outra(s) sessão(ões) revogada(s).", revoked)
        return revoked, None

    # ------------------------------------------------------------------
    # Sessões
    # ------------------------------------------------------------------

    def list_sessions(self, *, user_id: str, current_token: str | None):
        return self._sessions.list_sessions(user_id, current_token=current_token)

    def log_out_other_sessions(
        self, *, user_id: str, current_token: str | None
    ) -> tuple[int, AppError | None]:
        if not self._reauth.require(SensitiveAction.REVOKE_SESSIONS):
            return 0, AppError(
                AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha para encerrar as outras sessões."
            )
        if not current_token:
            return 0, AppError(AppErrorCode.INTERNAL_ERROR, "Sessão atual desconhecida.")
        revoked = self._sessions.delete_others_for_user(user_id, keep_token=current_token)
        logger.info("%s outra(s) sessão(ões) revogada(s) a pedido do usuário.", revoked)
        return revoked, None
