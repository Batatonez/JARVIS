"""`AccountManager`: fachada de contas locais — login/registro/logout/sessão
persistida entre execuções — e dono do ciclo de vida de
`JarvisCore`/`JarvisApplication` por usuário logado (memória isolada por
conta). Quem fala com isto é só o `JarvisBridge`; QML nunca importa nada
daqui, e nada aqui importa Qt.

    AccountManager
        ├── UserRepository / SessionRepository / ConversationRepository  (SQLite, services/)
        ├── session_store          (token local persistido — nunca a senha)
        ├── memory_migration       (memória legacy -> primeira conta)
        └── JarvisCore + JarvisApplication   (só existem enquanto alguém está logado)

Histórico visual (chats persistidos aqui) é diferente de sessão real do
Claude Agent SDK — ver `JarvisApplication.load_conversation_history()` para
o porquê isso importa.
"""

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from app.application import JarvisApplication
from app.core import JarvisCore
from app.entitlements import Entitlements, entitlements_for
from app.models import AppError, AppErrorCode, AppEvent, ConversationSummary, Message, MessageRole, User
from config.settings import Settings
from services import account_deletion, memory_migration, session_store
from services.account_service import AccountService
from services.ai_service import AIService
from services.chat_title_service import ChatTitleService
from services.conversation_repository import ConversationRepository
from services.email_change_service import (
    EmailChangeRequestResult,
    EmailChangeService,
    PendingEmailChange,
    PendingEmailChangeRepository,
)
from services.email_service import EmailService, create_email_service
from services.email_verification_repository import EmailVerificationRepository, VerificationChallenge
from services.email_verification_service import EmailVerificationService, VerificationRequestResult
from services.context_builder import sanitize_context
from services.local_database import connect
from services.login_throttle import AuthChannel, LoginThrottle, LoginThrottled
from services.long_term_memory import (
    LongTermMemoryRepository,
    extract_memories,
    format_memories_for_context,
)
from services.memory_service import MemoryService
from services.reauth import ReauthGuard
from services.provider_status_service import ProviderStatusService
from services.recovery_code_repository import RecoveryCodeRepository
from services.security_event_repository import (
    SecurityEvent,
    SecurityEventRepository,
    SecurityEventType,
)
from services.session_repository import SessionInfo, SessionRepository, describe_current_device
from services.two_factor_service import TwoFactorEnrollment, TwoFactorService, TwoFactorStatus
from services.user_repository import (
    AccountLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidUsernameError,
    UserRepository,
    UsernameAlreadyExistsError,
)
from services.user_settings_repository import KEY_MICROPHONE, UserSettingsRepository
from services.voice_service import VoiceService

logger = logging.getLogger(__name__)


class TwoFactorRequiredError(Exception):
    """A senha bateu, mas a conta tem 2FA ativo — nenhuma sessão foi criada
    ainda (item 52). O fluxo continua em `AccountManager.complete_two_factor`.

    Exceção (e não um retorno especial) de propósito: assim é impossível um
    chamador ignorar o segundo fator por engano e seguir tratando o retorno
    como "logado"."""


__all__ = [
    "AccountLockedError",
    "AccountManager",
    "EmailAlreadyRegisteredError",
    "InvalidCredentialsError",
    "TwoFactorRequiredError",
    "UsernameAlreadyExistsError",
]

# Eventos da Application Layer que indicam "uma mensagem nova pode existir"
# — gatilho para persistir (ver `_persist_new_messages`).
_MESSAGE_EVENTS = frozenset({"message.received", "response.completed"})


class AccountManager:
    def __init__(
        self,
        settings: Settings,
        *,
        connection: sqlite3.Connection | None = None,
        ai_service_factory: Callable[[], AIService] | None = None,
        voice_service_factory: Callable[[JarvisCore], VoiceService] | None = None,
        email_service: EmailService | None = None,
    ) -> None:
        """`ai_service_factory`/`voice_service_factory`/`email_service` só
        existem para os testes injetarem fakes (mesmo raciocínio de
        `JarvisCore`/`JarvisApplication` aceitarem serviços por parâmetro) —
        em produção nunca são passados, e o comportamento é idêntico."""
        self.settings = settings
        self._conn = connection or connect(settings.db_path)
        self._users = UserRepository(self._conn)
        self._sessions = SessionRepository(self._conn)
        self._conversations = ConversationRepository(self._conn)
        self._memories = LongTermMemoryRepository(self._conn)
        self._user_settings = UserSettingsRepository(self._conn)
        self._recovery_codes = RecoveryCodeRepository(self._conn)
        self._security_events = SecurityEventRepository(self._conn)
        self._ai_service_factory = ai_service_factory
        self._voice_service_factory = voice_service_factory

        # v1.5.0 — limitação de tentativas por IDENTIFICADOR, complementar ao
        # backoff por conta que já existe no `UserRepository` desde a v1.0. É
        # esta camada que cobre tentativas contra identificadores que não
        # existem (password spraying), que antes não eram contadas por
        # ninguém. Ver `services/login_throttle.py`.
        self.login_throttle = LoginThrottle()

        email = email_service or create_email_service(settings)
        self.verification = EmailVerificationService(
            EmailVerificationRepository(self._conn), email, app_name=settings.app_name
        )

        # v1.3 — uma única guarda de reautenticação recente para TODAS as
        # operações sensíveis (item 45). Todos os serviços abaixo recebem a
        # MESMA instância: confirmar a senha uma vez vale para a sequência de
        # ajustes que o usuário está fazendo, e invalidar (logout, troca de
        # senha) fecha a janela para todos de uma vez.
        self.reauth = ReauthGuard()
        self.account = AccountService(
            self._users, self._sessions, reauth=self.reauth, events=self._security_events
        )
        self.two_factor = TwoFactorService(
            self._users,
            self._recovery_codes,
            reauth=self.reauth,
            issuer=settings.app_name,
            events=self._security_events,
        )
        self.email_change = EmailChangeService(
            PendingEmailChangeRepository(self._conn),
            self._users,
            email,
            reauth=self.reauth,
            app_name=settings.app_name,
        )

        self._current_user: User | None = None
        self._current_session_token: str | None = None
        self._current_conversation_id: str | None = None
        self._persisted_message_ids: set[str] = set()
        # Conta que passou pela senha mas ainda deve o segundo fator. Só vive
        # em RAM e só entre `login()` e `complete_two_factor()`.
        self._pending_two_factor_user_id: str | None = None
        self._titles: ChatTitleService | None = None
        # Conversas que já receberam (ou dispensaram) título automático nesta
        # sessão — evita repetir a chamada a cada mensagem nova.
        self._auto_titled: set[str] = set()

        self.core: JarvisCore | None = None
        self.app: JarvisApplication | None = None
        self._event_queue: "asyncio.Queue[AppEvent] | None" = None
        self._event_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Estado público
    # ------------------------------------------------------------------

    @property
    def current_user(self) -> User | None:
        return self._current_user

    @property
    def is_authenticated(self) -> bool:
        return self._current_user is not None

    @property
    def current_conversation_id(self) -> str | None:
        return self._current_conversation_id

    def current_entitlements(self) -> Entitlements | None:
        return entitlements_for(self._current_user.plan) if self._current_user else None

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------

    async def try_auto_login(self) -> User | None:
        """Chamado uma vez na inicialização: se houver um token de sessão
        local válido, restaura o login sem pedir usuário/senha de novo. Um
        token ausente/inválido/expirado simplesmente devolve `None` — a UI
        mostra a tela de login normalmente, sem tratar isso como erro."""
        token = session_store.load_token(self.settings.session_token_path)
        if token is None:
            return None
        user_id = self._sessions.validate_session(token)
        if user_id is None:
            session_store.clear_token(self.settings.session_token_path)
            return None
        user = self._users.get_user(user_id)
        if user is None:
            session_store.clear_token(self.settings.session_token_path)
            return None
        # Marca este dispositivo como usado agora — é o que faz a tela
        # "Active Sessions" mostrar informação verdadeira (item 54).
        self._sessions.touch(token)
        await self._open_session(user, token)
        return user

    async def register(
        self, *, username: str, display_name: str, password: str, email: str | None = None
    ) -> User:
        """`email` é obrigatório para contas novas na prática (o HUD sempre
        envia), mas o parâmetro é opcional para não quebrar chamadas antigas
        e para permitir contas sem e-mail em testes/CLI. Uma conta recém
        criada nunca nasce verificada — ver `request_email_verification()`."""
        is_first_account = not self._users.has_any_user()
        user = self._users.create_user(
            username=username, display_name=display_name, password=password, email=email
        )

        if is_first_account:
            migrated = memory_migration.migrate_legacy_memory(
                legacy_profile_path=self.settings.profile_path,
                legacy_preferences_path=self.settings.preferences_path,
                target_memory_dir=memory_migration.user_memory_dir(self.settings.users_dir, user.id),
            )
            if migrated:
                logger.info("Memória legacy migrada para a primeira conta (%s): %s", user.username, migrated)

        token = self._sessions.create_session(user.id)
        session_store.save_token(self.settings.session_token_path, token)
        await self._open_session(user, token)
        return user

    async def login(self, *, identifier: str, password: str) -> User:
        """`identifier` é **username OU e-mail** (item 38) — quem resolve isso
        é o repositório, numa query só, para os dois caminhos custarem o
        mesmo tempo.

        Levanta `InvalidCredentialsError` (mesma mensagem para conta
        inexistente e senha errada), `AccountLockedError`/`LoginThrottled`
        (cooldown de força bruta) ou `TwoFactorRequiredError` (item 52: a senha
        bateu, mas nenhuma sessão é criada antes do segundo fator).

        v1.5.0 — o throttle por identificador é consultado ANTES de tocar o
        banco: uma tentativa já bloqueada não chega a custar um hash scrypt, e
        identificadores inexistentes (que não têm linha para contar falhas)
        passam a ser limitados como qualquer outro."""
        self.login_throttle.check(identifier, AuthChannel.PASSWORD)

        try:
            user = self._users.authenticate(identifier=identifier, password=password)
        except InvalidCredentialsError:
            self.login_throttle.register_failure(identifier, AuthChannel.PASSWORD)
            raise

        self.login_throttle.register_success(identifier, AuthChannel.PASSWORD)

        if self._users.is_totp_enabled(user.id):
            self._pending_two_factor_user_id = user.id
            raise TwoFactorRequiredError(
                "Digite o código do seu aplicativo autenticador."
            )

        return await self._establish_session(user)

    async def complete_two_factor(self, code: str) -> tuple[User | None, AppError | None]:
        """Segundo passo do login com 2FA. Só aqui a sessão é criada.

        O `user_id` vem de `self._pending_two_factor_user_id`, gravado quando
        a senha foi validada — nunca de um parâmetro. Sem isso, este método
        seria um caminho para logar em qualquer conta só sabendo um código."""
        user_id = self._pending_two_factor_user_id
        if user_id is None:
            return None, AppError(
                AppErrorCode.INTERNAL_ERROR, "Nenhum login aguardando verificação."
            )

        # O throttle do segundo fator é contado por `user_id` (não pelo
        # identificador digitado): nesta etapa a conta já é conhecida, e o
        # orçamento de tentativas é separado do da senha — errar o código do
        # autenticador não deve consumir tentativas de login.
        try:
            self.login_throttle.check(user_id, AuthChannel.TWO_FACTOR)
        except LoginThrottled as exc:
            self._security_events.record(
                user_id=user_id,
                event_type=SecurityEventType.LOGIN_BLOCKED,
                metadata={"channel": AuthChannel.TWO_FACTOR.value},
            )
            return None, AppError(AppErrorCode.TWO_FACTOR_RATE_LIMITED, str(exc))

        error = self.two_factor.verify(user_id=user_id, code=code)
        if error is not None:
            self.login_throttle.register_failure(user_id, AuthChannel.TWO_FACTOR)
            return None, error
        self.login_throttle.register_success(user_id, AuthChannel.TWO_FACTOR)

        user = self._users.get_user(user_id)
        if user is None:
            self._pending_two_factor_user_id = None
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Conta não encontrada.")

        self._pending_two_factor_user_id = None
        return await self._establish_session(user), None

    def cancel_two_factor(self) -> None:
        """Usuário desistiu na tela do segundo fator — descarta o login
        parcial. Sem isto, o pendente sobreviveria até o próximo login."""
        self._pending_two_factor_user_id = None

    @property
    def awaiting_two_factor(self) -> bool:
        return self._pending_two_factor_user_id is not None

    async def _establish_session(self, user: User) -> User:
        token = self._sessions.create_session(user.id)
        session_store.save_token(self.settings.session_token_path, token)
        platform_name, device_label = describe_current_device()
        self._security_events.record(
            user_id=user.id,
            event_type=SecurityEventType.LOGIN_SUCCEEDED,
            metadata={"platform": platform_name, "device_label": device_label},
        )
        await self._open_session(user, token)
        return user

    # ------------------------------------------------------------------
    # Verificação de e-mail (v1.0)
    #
    # Uma conta NÃO verificada continua funcionando normalmente nesta versão
    # (chat, memória, chats persistidos) — a verificação existe para a conta
    # ser recuperável/confiável no futuro, não como paywall. Bloquear o uso
    # do app numa conta local, sem backend, só puniria quem não configurou
    # SMTP. Ver docs/security.md.
    # ------------------------------------------------------------------

    async def request_email_verification(self, *, force: bool = False) -> VerificationRequestResult:
        """Envia (ou reenvia) o código para o e-mail da conta atual.
        `force=True` pula o cooldown de reenvio — usado logo após o cadastro,
        quando ainda não existe desafio anterior."""
        user = self._current_user
        if user is None:
            return VerificationRequestResult(
                sent=False,
                error=AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada."),
            )
        if not user.email:
            return VerificationRequestResult(
                sent=False,
                error=AppError(
                    AppErrorCode.INTERNAL_ERROR,
                    "Esta conta não tem e-mail cadastrado. Adicione um e-mail antes de verificar.",
                ),
            )
        return await self.verification.request_code(user_id=user.id, email=user.email, force=force)

    def verify_email_code(self, code: str) -> AppError | None:
        """`None` = verificado. O `User` em memória é atualizado para que o
        HUD reflita o novo estado sem precisar relogar."""
        user = self._current_user
        if user is None:
            return AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        error = self.verification.verify(user_id=user.id, code=code)
        if error is not None:
            return error
        self._users.mark_email_verified(user.id)
        self._current_user = self._users.get_user(user.id) or user
        logger.info("E-mail verificado para a conta '%s'.", user.username)
        return None

    def active_verification_challenge(self) -> VerificationChallenge | None:
        """Desafio ativo da conta atual — os dois contadores do HUD
        (expiração e reenvio) derivam daqui, então fechar e reabrir o JARVIS
        mostra o tempo real restante, não um timer reiniciado."""
        if self._current_user is None:
            return None
        return self.verification.active_challenge(self._current_user.id)

    def set_email(self, email: str) -> User | None:
        """Define/troca o e-mail da conta atual (contas legacy da v0.9 nascem
        sem e-mail). Sempre volta ao estado não-verificado."""
        if self._current_user is None:
            return None
        user = self._users.set_email(self._current_user.id, email)
        if user is not None:
            self._current_user = user
        return user

    async def logout(self) -> None:
        """Sai da conta atual. Em ordem: encerra a Application Layer (o que
        cancela a requisição de IA pendente e desliga o microfone/TTS via
        `JarvisApplication.stop()`), invalida a sessão no banco, apaga o
        token local, e limpa o estado sensível em RAM.

        NÃO apaga a conta, os chats nem a memória — só sai."""
        await self._teardown_session()
        if self._current_session_token is not None:
            self._sessions.delete_session(self._current_session_token)
        session_store.clear_token(self.settings.session_token_path)
        # A janela de reautenticação recente nunca sobrevive ao logout.
        self.reauth.invalidate()
        self._pending_two_factor_user_id = None
        self._current_user = None
        self._current_session_token = None
        self._current_conversation_id = None
        self._persisted_message_ids = set()
        self._auto_titled = set()
        self._titles = None

    async def shutdown(self) -> None:
        """Encerramento do processo (janela fechando) — diferente de
        `logout()`: mantém a sessão válida para o próximo `python -m frontend`
        continuar logado."""
        await self._teardown_session()

    async def _open_session(self, user: User, token: str) -> None:
        self._current_user = user
        self._current_session_token = token

        user_memory_dir = memory_migration.user_memory_dir(self.settings.users_dir, user.id)
        user_memory_dir.mkdir(parents=True, exist_ok=True)
        memory_service = MemoryService(user_memory_dir / "profile.md", user_memory_dir / "preferences.md")

        ai_service = self._ai_service_factory() if self._ai_service_factory else None
        self.core = JarvisCore(settings=self.settings, memory_service=memory_service, ai_service=ai_service)
        voice_service = self._voice_service_factory(self.core) if self._voice_service_factory else None
        self.app = JarvisApplication(
            self.core,
            voice_service=voice_service,
            # v1.1 — é aqui que a memória de longo prazo atravessa conversas:
            # a Application Layer não sabe quem é o usuário, então quem
            # resolve isso é o AccountManager, que sabe.
            memory_context_provider=self._memory_context_for,
        )
        await self.app.start()

        self._event_queue = self.app.subscribe()
        self._event_task = asyncio.ensure_future(self._consume_events())

        conversations = self._conversations.list_conversations(user.id)
        if conversations:
            await self.open_conversation(conversations[0].id)
        else:
            self._current_conversation_id = None
            self._persisted_message_ids = set()

        logger.info("Sessão aberta para '%s'.", user.username)

    async def _teardown_session(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_task
            self._event_task = None
        if self.app is not None:
            await self.app.stop()
        self.app = None
        self.core = None

    # ------------------------------------------------------------------
    # Conversas
    # ------------------------------------------------------------------

    def list_conversations(self) -> list[ConversationSummary]:
        if self._current_user is None:
            return []
        return self._conversations.list_conversations(self._current_user.id)

    def search_conversations(self, query: str) -> list[ConversationSummary]:
        if self._current_user is None:
            return []
        return self._conversations.search_conversations(self._current_user.id, query)

    async def start_new_conversation(self) -> str | None:
        if self._current_user is None or self.app is None:
            return None
        conversation_id = self._conversations.create_conversation(self._current_user.id)
        self._current_conversation_id = conversation_id
        self._persisted_message_ids = set()
        await self.app.new_conversation()
        return conversation_id

    async def open_conversation(self, conversation_id: str) -> bool:
        if self._current_user is None or self.app is None:
            return False
        messages = self._conversations.get_conversation(conversation_id, self._current_user.id)
        if messages is None:
            return False
        self._current_conversation_id = conversation_id
        self._persisted_message_ids = {message.id for message in messages}
        await self.app.load_conversation_history(messages)
        return True

    def rename_conversation(self, conversation_id: str, title: str) -> bool:
        """Rename manual (item 18). Marca `manual_title` no banco, e a partir
        daí o título automático nunca sobrescreve (item 23)."""
        if self._current_user is None:
            return False
        renamed = self._conversations.rename_conversation(
            conversation_id, self._current_user.id, title
        )
        if renamed:
            # Já tem nome definido pelo dono: não gastar chamada de IA nem
            # tentar renomear de novo nesta sessão.
            self._auto_titled.add(conversation_id)
        return renamed

    async def _auto_title_task(self) -> None:
        """Wrapper que nunca deixa uma falha de título escapar para o
        consumidor de eventos — nomear um chat não pode derrubar o chat."""
        try:
            title = await self._maybe_generate_title()
        except Exception:
            logger.exception("Falha ao gerar título automático; conversa segue normalmente.")
            return
        if title and self.app is not None:
            # Avisa o frontend para a sidebar recarregar com o nome novo.
            self.app.emit_external_event("conversation.retitled", {"title": title})

    async def _maybe_generate_title(self) -> str | None:
        """Título automático depois da primeira troca (item 19).

        Roda uma vez por conversa e só quando já existem a primeira mensagem
        do usuário E a primeira resposta — é o contexto mínimo para inferir o
        assunto em vez de copiar a pergunta. Falhar é silencioso: o chat
        mantém o título derivado do texto."""
        conversation_id = self._current_conversation_id
        if conversation_id is None or self._current_user is None or self.core is None:
            return None
        if conversation_id in self._auto_titled:
            return None
        if self._conversations.has_manual_title(conversation_id, self._current_user.id):
            self._auto_titled.add(conversation_id)
            return None
        if self.app is None:
            return None

        messages = self.app.get_messages()
        first_user = next((m for m in messages if m.role is MessageRole.USER), None)
        first_assistant = next((m for m in messages if m.role is MessageRole.ASSISTANT), None)
        if first_user is None or first_assistant is None:
            return None

        # Marca ANTES de chamar: se a IA demorar e outra resposta chegar, não
        # queremos duas gerações concorrentes para a mesma conversa.
        self._auto_titled.add(conversation_id)

        titles = ChatTitleService(self.core.ai_service)
        title = await titles.suggest(
            user_message=first_user.content, assistant_message=first_assistant.content
        )
        if not title:
            return None
        if self._conversations.set_automatic_title(
            conversation_id, self._current_user.id, title
        ):
            logger.info("Título automático aplicado à conversa.")
            return title
        return None

    async def delete_conversation(self, conversation_id: str) -> bool:
        if self._current_user is None:
            return False
        deleted = self._conversations.delete_conversation(conversation_id, self._current_user.id)
        if deleted and conversation_id == self._current_conversation_id and self.app is not None:
            self._current_conversation_id = None
            self._persisted_message_ids = set()
            await self.app.new_conversation()
        return deleted

    async def _consume_events(self) -> None:
        assert self._event_queue is not None
        try:
            while True:
                event = await self._event_queue.get()
                try:
                    self._persist_new_messages(event)
                except Exception:
                    logger.exception("Erro ao persistir mensagem (evento '%s').", event.type)
                if event.type == "response.completed":
                    # Título automático depois da primeira troca (item 19).
                    # Numa task separada: gerar o título chama a IA, e o
                    # consumidor de eventos não pode ficar bloqueado nisso.
                    asyncio.ensure_future(self._auto_title_task())
        finally:
            if self.app is not None:
                self.app.unsubscribe(self._event_queue)

    def _persist_new_messages(self, event: AppEvent) -> None:
        if self.app is None or self._current_user is None:
            return
        if event.type == "response.regenerated":
            self._persist_regenerated_message(event)
            return
        if event.type not in _MESSAGE_EVENTS:
            return
        messages = self.app.get_messages()
        if not messages:
            return

        if self._current_conversation_id is None:
            # A conversa nasce com o título PADRÃO ("Nova conversa"), nunca
            # com a primeira mensagem copiada.
            #
            # Este era o bug do item 5 da v1.3.2: aqui havia
            # `derive_title(messages[0].content)`, que gravava a própria
            # pergunta como nome do chat. Como o título automático só roda
            # depois da primeira resposta — e não roda de jeito nenhum sem IA
            # configurada — o nome copiado era o que ficava para sempre. Ver
            # `_maybe_generate_title`.
            self._current_conversation_id = self._conversations.create_conversation(
                self._current_user.id
            )

        for message in messages:
            if message.id in self._persisted_message_ids:
                continue
            saved = self._conversations.save_message(self._current_conversation_id, self._current_user.id, message)
            if saved:
                self._persisted_message_ids.add(message.id)
                self._extract_long_term_memory(message)

    def _persist_regenerated_message(self, event: AppEvent) -> None:
        """"Regenerate" reescreve uma resposta que JÁ existe no banco: o id
        não muda, então `_persist_new_messages` a puluaria (já está em
        `_persisted_message_ids`). Aqui atualizamos a linha existente."""
        message_id = event.payload.get("message_id")
        if not message_id or self._current_conversation_id is None:
            return
        message = self.app.get_messages_by_id(str(message_id))
        if message is None:
            return
        self._conversations.update_message_content(
            self._current_conversation_id, self._current_user.id, message.id, message.content
        )

    def _extract_long_term_memory(self, message: Message) -> None:
        """Guarda fatos duráveis ditos pelo usuário (v1.1). Só mensagens do
        USUÁRIO: o que a IA respondeu não é fato sobre o usuário, e memorizar
        isso realimentaria alucinação. Falha aqui nunca derruba o chat."""
        if message.role is not MessageRole.USER or self._current_user is None:
            return
        try:
            for category, content in extract_memories(message.content):
                self._memories.remember(
                    user_id=self._current_user.id,
                    category=category,
                    content=content,
                    source_conversation_id=self._current_conversation_id,
                )
                logger.info("Memória de longo prazo registrada (%s).", category.value)
        except Exception:
            logger.exception("Falha ao extrair memória de longo prazo; conversa segue normalmente.")

    # ------------------------------------------------------------------
    # Memória de longo prazo (v1.1)
    # ------------------------------------------------------------------

    def _memory_context_for(self, message: str) -> str:
        """Memória relevante do usuário LOGADO, formatada para o contexto.

        O `user_id` vem sempre de `self._current_user` — nunca de algo que
        a Application Layer ou o frontend possa influenciar. É isso que
        garante que a memória de um usuário jamais entre no contexto de
        outro (testado em `tests/test_long_term_memory.py`)."""
        if self._current_user is None:
            return ""
        try:
            relevant = self._memories.relevant_for(user_id=self._current_user.id, query=message)
        except Exception:
            logger.exception("Falha ao recuperar memória de longo prazo; seguindo sem ela.")
            return ""
        # A sanitização de segredos continua valendo: este texto se junta ao
        # que já passa por `context_builder` no system prompt.
        return sanitize_context(format_memories_for_context(relevant))

    # ------------------------------------------------------------------
    # Account Settings (v1.3)
    #
    # Todo método aqui resolve `user_id` a partir de `self._current_user` — o
    # frontend NUNCA escolhe em qual conta a operação acontece (item 60).
    # ------------------------------------------------------------------

    def confirm_password(self, password: str) -> AppError | None:
        """Abre a janela de reautenticação recente. Porta de entrada de todas
        as operações sensíveis (item 45)."""
        if self._current_user is None:
            return AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        return self.account.confirm_password(
            user_id=self._current_user.id, password=password
        )

    def reauth_valid(self) -> bool:
        return self.reauth.is_valid()

    def change_display_name(self, display_name: str) -> tuple[User | None, AppError | None]:
        if self._current_user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        user, error = self.account.change_display_name(
            user_id=self._current_user.id, display_name=display_name
        )
        if user is not None:
            self._current_user = user
        return user, error

    def change_username(self, username: str) -> tuple[User | None, AppError | None]:
        if self._current_user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        user, error = self.account.change_username(
            user_id=self._current_user.id, username=username
        )
        if user is not None:
            self._current_user = user
        return user, error

    def change_password(
        self, *, current_password: str, new_password: str, confirm_password: str
    ) -> tuple[int, AppError | None]:
        """Troca a senha e derruba as OUTRAS sessões (item 44). A sessão deste
        aparelho sobrevive — o usuário não é expulso de onde acabou de agir."""
        if self._current_user is None:
            return 0, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        return self.account.change_password(
            user_id=self._current_user.id,
            current_password=current_password,
            new_password=new_password,
            confirm_password=confirm_password,
            current_token=self._current_session_token,
        )

    # --- Troca de e-mail (duas fases) ---

    async def request_email_change(self, new_email: str, *, force: bool = False) -> EmailChangeRequestResult:
        if self._current_user is None:
            return EmailChangeRequestResult(
                sent=False,
                error=AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada."),
            )
        result = await self.email_change.request_change(
            user_id=self._current_user.id, new_email=new_email, force=force
        )
        if result.error is None:
            self._security_events.record(
                user_id=self._current_user.id,
                event_type=SecurityEventType.EMAIL_CHANGE_REQUESTED,
                # Só o e-mail MASCARADO: o endereço completo já está na conta,
                # e repeti-lo no log de atividade só espalharia dado pessoal.
                metadata={"masked_email": mask_email(new_email)},
            )
        return result

    async def confirm_email_change(self, code: str) -> AppError | None:
        if self._current_user is None:
            return AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        error = await self.email_change.confirm_change(user_id=self._current_user.id, code=code)
        if error is None:
            self._security_events.record(
                user_id=self._current_user.id, event_type=SecurityEventType.EMAIL_CHANGED
            )
            self._current_user = self._users.get_user(self._current_user.id) or self._current_user
        return error

    def active_email_change(self) -> PendingEmailChange | None:
        if self._current_user is None:
            return None
        return self.email_change.active_request(self._current_user.id)

    # --- 2FA ---

    def two_factor_status(self) -> TwoFactorStatus | None:
        if self._current_user is None:
            return None
        return self.two_factor.status(self._current_user.id)

    def start_two_factor_enrollment(self) -> tuple[TwoFactorEnrollment | None, AppError | None]:
        if self._current_user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        # O rótulo da conta no autenticador usa o e-mail quando existe (é o
        # que o usuário reconhece na lista do app), senão o username.
        account_name = self._current_user.email or self._current_user.username
        return self.two_factor.start_enrollment(
            user_id=self._current_user.id, account_name=account_name
        )

    def confirm_two_factor_enrollment(self, code: str) -> tuple[list[str] | None, AppError | None]:
        if self._current_user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        codes, error = self.two_factor.confirm_enrollment(
            user_id=self._current_user.id, code=code
        )
        if error is None:
            # Só a QUANTIDADE de códigos gerados. Os códigos em si nunca
            # tocam o log de atividade — nem hasheados.
            self._security_events.record(
                user_id=self._current_user.id,
                event_type=SecurityEventType.TWO_FACTOR_ENABLED,
                metadata={"codes_remaining": len(codes or [])},
            )
            self._current_user = self._users.get_user(self._current_user.id) or self._current_user
        return codes, error

    def regenerate_recovery_codes(self, code: str) -> tuple[list[str] | None, AppError | None]:
        if self._current_user is None:
            return None, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        return self.two_factor.regenerate_recovery_codes(
            user_id=self._current_user.id, code=code
        )

    def disable_two_factor(self, code: str) -> AppError | None:
        if self._current_user is None:
            return AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        error = self.two_factor.disable(user_id=self._current_user.id, code=code)
        if error is None:
            self._security_events.record(
                user_id=self._current_user.id, event_type=SecurityEventType.TWO_FACTOR_DISABLED
            )
            self._current_user = self._users.get_user(self._current_user.id) or self._current_user
        return error

    # --- Sessões ---

    def list_sessions(self) -> list[SessionInfo]:
        if self._current_user is None:
            return []
        return self.account.list_sessions(
            user_id=self._current_user.id, current_token=self._current_session_token
        )

    def log_out_other_sessions(self) -> tuple[int, AppError | None]:
        if self._current_user is None:
            return 0, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        return self.account.log_out_other_sessions(
            user_id=self._current_user.id, current_token=self._current_session_token
        )

    async def revoke_session(self, session_id: str) -> tuple[bool, AppError | None]:
        """Encerra UMA sessão da conta atual (v1.5.0). Devolve
        `(deslogou_este_dispositivo, erro)`.

        O `user_id` vem de `self._current_user` — o frontend escolhe QUAL
        sessão, nunca DE QUEM. Se a sessão encerrada for a deste dispositivo,
        o logout acontece aqui mesmo: deixar a UI acreditando que continua
        logada com um token já revogado seria pior do que não ter o botão."""
        if self._current_user is None:
            return False, AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")
        was_current, error = self.account.revoke_session(
            user_id=self._current_user.id,
            session_id=session_id,
            current_token=self._current_session_token,
        )
        if error is not None:
            return False, error
        if was_current:
            # A sessão no banco já morreu; `logout()` cuida do resto (encerra
            # a Application Layer, apaga o token local, limpa a RAM).
            await self.logout()
        return was_current, None

    # --- Atividade de segurança (v1.5) ---

    def list_security_events(self, *, limit: int = 50, offset: int = 0) -> list[SecurityEvent]:
        if self._current_user is None:
            return []
        return self.account.list_security_events(
            user_id=self._current_user.id, limit=limit, offset=offset
        )

    # --- Disponibilidade de identidade no cadastro (v1.5) ---
    #
    # Estes dois são os ÚNICOS métodos públicos daqui que funcionam sem conta
    # autenticada — por necessidade: quem está criando conta ainda não tem
    # uma. Eles não revelam nada além de "este username/e-mail está livre",
    # que é justamente o que o cadastro precisa dizer para ser usável.

    def check_username_available(self, username: str) -> tuple[bool, str]:
        return self.account.check_username_available(username)

    def check_email_available(self, email: str) -> tuple[bool, str]:
        return self.account.check_email_available(email)

    # --- Providers de IA (v1.5) ---

    def provider_status(self) -> ProviderStatusService | None:
        """`ProviderStatusService` da sessão atual, ou `None` quando não há IA
        roteada por `ProviderRouter` (placeholder sem chave configurada, ou
        Claude Agent SDK). Construído sob demanda: a tela de providers é
        aberta raramente, e o serviço não guarda estado que precise viver
        junto com a sessão."""
        router = getattr(getattr(self.core, "ai_service", None), "_router", None)
        return ProviderStatusService(router) if router is not None else None

    # --- Exclusão da conta ---

    async def delete_current_account(
        self, *, password: str, confirmation: str, two_factor_code: str = ""
    ) -> AppError | None:
        """Apaga SOMENTE a conta atual (item 56). Ordem das travas: senha ->
        segundo fator (se ativo) -> texto `DELETE` digitado. Só depois disso
        alguma coisa é removida."""
        user = self._current_user
        if user is None:
            return AppError(AppErrorCode.INTERNAL_ERROR, "Nenhuma conta autenticada.")

        if not self._users.verify_password_for(user.id, password):
            return AppError(AppErrorCode.INVALID_PASSWORD, "Senha incorreta.")

        if self._users.is_totp_enabled(user.id):
            error = self.two_factor.verify(user_id=user.id, code=two_factor_code)
            if error is not None:
                return error

        if (confirmation or "").strip().upper() != "DELETE":
            return AppError(
                AppErrorCode.CONFIRMATION_MISMATCH,
                "Digite DELETE para confirmar a exclusão da conta.",
            )

        # Registrado antes de apagar: o `ON DELETE CASCADE` leva junto os
        # eventos desta conta, então este registro existe só para o intervalo
        # em que a exclusão ainda pode falhar (e aí ele permanece, mostrando
        # que houve uma tentativa).
        self._security_events.record(
            user_id=user.id, event_type=SecurityEventType.ACCOUNT_DELETION_STARTED
        )

        # Encerra a Application Layer antes de apagar: sem isto, o consumidor
        # de eventos poderia tentar persistir uma mensagem numa conta que já
        # não existe mais.
        await self._teardown_session()
        try:
            account_deletion.delete_account(
                self._conn, user_id=user.id, users_dir=self.settings.users_dir
            )
        except account_deletion.AccountDeletionError as exc:
            logger.warning("Falha ao apagar a conta: %s", exc)
            return AppError(AppErrorCode.INTERNAL_ERROR, str(exc))

        session_store.clear_token(self.settings.session_token_path)
        self.reauth.invalidate()
        self._current_user = None
        self._current_session_token = None
        self._current_conversation_id = None
        self._persisted_message_ids = set()
        self._auto_titled = set()
        logger.info("Conta apagada a pedido do usuário.")
        return None

    # ------------------------------------------------------------------
    # Preferências (v1.3)
    # ------------------------------------------------------------------

    def preferred_microphone_key(self) -> str | None:
        if self._current_user is None:
            return None
        return self._user_settings.get(self._current_user.id, KEY_MICROPHONE)

    def set_preferred_microphone(self, device_key: str | None) -> None:
        """Guarda a CHAVE estável do dispositivo (host API + nome), nunca o
        índice — ver `services/audio_devices.py`, item 14."""
        if self._current_user is None:
            return
        if device_key:
            self._user_settings.set(self._current_user.id, KEY_MICROPHONE, device_key)
        else:
            self._user_settings.clear(self._current_user.id, KEY_MICROPHONE)

    def list_memories(self):
        if self._current_user is None:
            return []
        return self._memories.list_memories(self._current_user.id)

    def forget_memory(self, memory_id: str) -> bool:
        if self._current_user is None:
            return False
        return self._memories.forget(user_id=self._current_user.id, memory_id=memory_id)
