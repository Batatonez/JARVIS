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
from services import memory_migration, session_store
from services.ai_service import AIService
from services.conversation_repository import ConversationRepository, derive_title
from services.email_service import EmailService, create_email_service
from services.email_verification_repository import EmailVerificationRepository, VerificationChallenge
from services.email_verification_service import EmailVerificationService, VerificationRequestResult
from services.context_builder import sanitize_context
from services.local_database import connect
from services.long_term_memory import (
    LongTermMemoryRepository,
    extract_memories,
    format_memories_for_context,
)
from services.memory_service import MemoryService
from services.session_repository import SessionRepository
from services.user_repository import (
    AccountLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    UserRepository,
    UsernameAlreadyExistsError,
)
from services.voice_service import VoiceService

logger = logging.getLogger(__name__)

__all__ = [
    "AccountLockedError",
    "AccountManager",
    "EmailAlreadyRegisteredError",
    "InvalidCredentialsError",
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
        self._ai_service_factory = ai_service_factory
        self._voice_service_factory = voice_service_factory
        self.verification = EmailVerificationService(
            EmailVerificationRepository(self._conn),
            email_service or create_email_service(settings),
            app_name=settings.app_name,
        )

        self._current_user: User | None = None
        self._current_session_token: str | None = None
        self._current_conversation_id: str | None = None
        self._persisted_message_ids: set[str] = set()

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

    async def login(self, *, username: str, password: str) -> User:
        # Levanta InvalidCredentialsError (usuário/senha) ou AccountLockedError
        # (cooldown de força bruta) — ver services/user_repository.py.
        user = self._users.authenticate(username=username, password=password)
        token = self._sessions.create_session(user.id)
        session_store.save_token(self.settings.session_token_path, token)
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
        self._current_user = None
        self._current_session_token = None
        self._current_conversation_id = None
        self._persisted_message_ids = set()

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
        if self._current_user is None:
            return False
        return self._conversations.rename_conversation(conversation_id, self._current_user.id, title)

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
        finally:
            if self.app is not None:
                self.app.unsubscribe(self._event_queue)

    def _persist_new_messages(self, event: AppEvent) -> None:
        if event.type not in _MESSAGE_EVENTS or self.app is None or self._current_user is None:
            return
        messages = self.app.get_messages()
        if not messages:
            return

        if self._current_conversation_id is None:
            title = derive_title(messages[0].content)
            self._current_conversation_id = self._conversations.create_conversation(
                self._current_user.id, title=title
            )

        for message in messages:
            if message.id in self._persisted_message_ids:
                continue
            saved = self._conversations.save_message(self._current_conversation_id, self._current_user.id, message)
            if saved:
                self._persisted_message_ids.add(message.id)
                self._extract_long_term_memory(message)

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

    def list_memories(self):
        if self._current_user is None:
            return []
        return self._memories.list_memories(self._current_user.id)

    def forget_memory(self, memory_id: str) -> bool:
        if self._current_user is None:
            return False
        return self._memories.forget(user_id=self._current_user.id, memory_id=memory_id)
