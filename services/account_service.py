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
from services import password_policy
from services.reauth import ReauthGuard, SensitiveAction
from services.security_event_repository import SecurityEventRepository, SecurityEventType
from services.session_repository import SessionRepository
from services.user_repository import (
    InvalidEmailError,
    InvalidUsernameError,
    UserRepository,
    UsernameAlreadyExistsError,
    validate_email,
    validate_username,
)

logger = logging.getLogger(__name__)

# A política de senha em si mora em `services/password_policy.py` desde a
# v1.5.0 (comprimento, senha comum, senha derivada dos dados da conta). Estes
# nomes continuam exportados daqui porque é daqui que o resto do sistema
# sempre os importou — mover a política não deveria obrigar cada chamador a
# saber que ela mudou de arquivo.
PASSWORD_MIN_LENGTH = password_policy.MIN_LENGTH
InvalidPasswordError = password_policy.InvalidPasswordError
validate_password = password_policy.validate_password


class AccountService:
    def __init__(
        self,
        users: UserRepository,
        sessions: SessionRepository,
        *,
        reauth: ReauthGuard,
        events: SecurityEventRepository | None = None,
    ) -> None:
        """`events` é opcional para que testes e chamadas de CLI que só querem
        exercitar a política de conta não precisem montar a tabela de
        atividade. Em produção o `AccountManager` sempre passa o repositório
        real — registrar atividade não é opcional para o usuário final."""
        self._users = users
        self._sessions = sessions
        self._reauth = reauth
        self._events = events

    def _record(self, user_id: str, event_type: SecurityEventType, **metadata) -> None:
        if self._events is not None:
            self._events.record(user_id=user_id, event_type=event_type, metadata=metadata)

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
        # A política precisa dos dados da conta para recusar uma senha
        # derivada deles (v1.5.0). O `user_id` é o da sessão autenticada, então
        # nunca há como avaliar a senha de uma conta contra os dados de outra.
        owner = self._users.get_user(user_id)
        try:
            validate_password(
                new_password,
                username=owner.username if owner else "",
                email=owner.email or "" if owner else "",
                display_name=owner.display_name if owner else "",
            )
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
        self._record(user_id, SecurityEventType.PASSWORD_CHANGED, session_count=revoked)
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
        self._record(user_id, SecurityEventType.OTHER_SESSIONS_REVOKED, session_count=revoked)
        logger.info("%s outra(s) sessão(ões) revogada(s) a pedido do usuário.", revoked)
        return revoked, None

    def revoke_session(
        self, *, user_id: str, session_id: str, current_token: str | None
    ) -> tuple[bool, AppError | None]:
        """Revoga UMA sessão (v1.5.0). Devolve `(era_a_sessão_atual, erro)` —
        o chamador usa o primeiro valor para deslogar imediatamente quando o
        usuário encerrou o próprio dispositivo.

        O `session_id` vem da tela, mas o `user_id` vem da sessão autenticada:
        um `session_id` de outra conta simplesmente não casa no DELETE (ver
        `SessionRepository.delete_by_session_id`)."""
        if not self._reauth.require(SensitiveAction.REVOKE_SESSIONS):
            return False, AppError(
                AppErrorCode.REAUTH_REQUIRED, "Confirme sua senha para encerrar esta sessão."
            )
        if not self._sessions.delete_by_session_id(user_id, session_id):
            # Mesma mensagem para "não existe" e "é de outra conta": diferenciar
            # confirmaria a existência de uma sessão alheia.
            return False, AppError(AppErrorCode.INTERNAL_ERROR, "Sessão não encontrada.")

        was_current = bool(
            current_token and self._sessions.session_id_for_token(current_token) == session_id
        )
        self._record(user_id, SecurityEventType.SESSION_REVOKED, session_count=1)
        logger.info("Uma sessão foi revogada a pedido do usuário (atual=%s).", was_current)
        return was_current, None

    # ------------------------------------------------------------------
    # Atividade de segurança
    # ------------------------------------------------------------------

    def list_security_events(self, *, user_id: str, limit: int = 50, offset: int = 0):
        """Só os eventos DESTA conta. Não existe variante sem `user_id`."""
        if self._events is None:
            return []
        return self._events.list_events(user_id, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Disponibilidade de identidade (cadastro)
    # ------------------------------------------------------------------

    def check_username_available(self, username: str) -> tuple[bool, str]:
        """Feedback instantâneo do cadastro (v1.5.0). Devolve
        `(disponível, mensagem)`.

        **Não é a garantia de unicidade** e não substitui validação nenhuma:
        o `create_user` revalida tudo no submit, e o índice UNIQUE do banco
        continua sendo a autoridade final — é ele que fecha a janela de
        corrida entre esta consulta e o INSERT. Aqui é só usabilidade.

        Dizer que um username específico está em uso é legítimo (username é
        escolhido publicamente e o cadastro fica impossível sem esse retorno);
        o que nunca acontece é o LOGIN diferenciar conta inexistente de senha
        errada — ver `UserRepository.authenticate`."""
        try:
            cleaned = validate_username(username)
        except InvalidUsernameError as exc:
            return False, str(exc)
        if self._users.find_by_username(cleaned) is not None:
            return False, "Esse username já está em uso."
        return True, "Disponível."

    def check_email_available(self, email: str) -> tuple[bool, str]:
        """Mesma ideia do username, para o e-mail."""
        try:
            cleaned = validate_email(email)
        except InvalidEmailError as exc:
            return False, str(exc)
        if self._users.email_in_use(cleaned):
            return False, "Esse e-mail já está em uso."
        return True, "Disponível."
