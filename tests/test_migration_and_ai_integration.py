"""Migração de banco (v0.9 -> v1.0) e a integração de ponta a ponta
`JarvisApplication -> AIService -> ProviderRouter -> provider fake`.

Nenhum teste toca rede: o provider é um `FakeHttpTransport`
(`tests/test_provider_router.py`) por dentro do `OpenRouterProvider` real,
então o caminho exercitado é o de produção inteiro, menos o socket.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.models import ResponseStatus
from services.ai_service import create_ai_service
from services.local_database import (
    MigrationError,
    SCHEMA_VERSION,
    _MIGRATION_1,
    _statements,
    connect,
    migrate,
)
from services.provider_ai_service import ProviderRouterAIService
from services.providers.openrouter_provider import FREE_MODEL, OpenRouterProvider
from services.providers.registry import ProviderRegistry
from services.providers.router import ProviderRouter
from services.session_repository import hash_token
from tests.helpers import build_isolated_application, build_isolated_settings
from tests.test_provider_router import FakeHttpTransport, _openrouter_success_response


def _build_v09_database(path: Path) -> None:
    """Reproduz um banco exatamente como a v0.9 o deixaria: schema original,
    `user_version = 0`, sessão com token em claro."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    for statement in _statements(_MIGRATION_1):
        conn.execute(statement)
    conn.execute(
        "INSERT INTO users VALUES ('u1','alice','Alice','scrypt$hash','free','2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('token-em-claro','u1','2026-01-01T00:00:00+00:00','2099-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO conversations VALUES ('c1','u1','Conversa da v0.9','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
    )
    conn.execute("INSERT INTO messages VALUES ('m1','c1','user','ola mundo','2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "jarvis.db"

    def test_fresh_database_lands_on_current_schema_version(self) -> None:
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        finally:
            conn.close()

    def test_v09_data_survives_migration(self) -> None:
        _build_v09_database(self.db_path)
        conn = connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

            user = conn.execute("SELECT * FROM users").fetchone()
            self.assertEqual(user["username"], "alice")
            self.assertIsNone(user["email"])  # coluna nova, conta legacy
            self.assertEqual(user["email_verified"], 0)
            self.assertEqual(user["failed_login_attempts"], 0)

            self.assertEqual(conn.execute("SELECT content FROM messages").fetchone()["content"], "ola mundo")
            self.assertEqual(
                conn.execute("SELECT title FROM conversations").fetchone()["title"], "Conversa da v0.9"
            )
        finally:
            conn.close()

    def test_existing_session_keeps_working_after_migration(self) -> None:
        """Auto-login não pode quebrar: o token que já está no disco do
        usuário precisa continuar resolvendo depois da migração."""
        _build_v09_database(self.db_path)
        conn = connect(self.db_path)
        try:
            from services.session_repository import SessionRepository

            resolved = SessionRepository(conn).validate_session("token-em-claro")
            self.assertEqual(resolved, "u1")

            stored = dict(conn.execute("SELECT * FROM sessions").fetchone())
            self.assertNotIn("token", stored)
            self.assertEqual(stored["token_hash"], hash_token("token-em-claro"))
        finally:
            conn.close()

    def test_migration_is_idempotent(self) -> None:
        _build_v09_database(self.db_path)
        first = connect(self.db_path)
        first.close()
        second = connect(self.db_path)
        try:
            self.assertEqual(second.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(second.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        finally:
            second.close()

    def test_failed_migration_rolls_back_and_preserves_data(self) -> None:
        """Uma migração que explode no meio não pode deixar o banco
        meio-migrado nem perder dados."""
        _build_v09_database(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row

        def _explode(_connection: sqlite3.Connection) -> None:
            _connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
            raise RuntimeError("falha simulada no meio da migração")

        import services.local_database as db_module

        original = db_module._MIGRATIONS
        db_module._MIGRATIONS = (db_module._apply_migration_1, _explode)
        try:
            with self.assertRaises(MigrationError):
                migrate(conn)

            # Versão não avançou e a coluna parcial foi revertida.
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            self.assertNotIn("email", columns)
            # E o dado do usuário continua lá.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        finally:
            db_module._MIGRATIONS = original
            conn.close()

    def test_database_from_the_future_is_refused_without_touching_it(self) -> None:
        conn = connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        conn.commit()
        try:
            with self.assertRaises(MigrationError):
                migrate(conn)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 5)
        finally:
            conn.close()


def _router_with_fake_transport(*, response=None, api_key: str = "sk-or-fake") -> ProviderRouter:
    registry = ProviderRegistry()
    registry.register(
        OpenRouterProvider(
            api_key=api_key,
            transport=FakeHttpTransport(response=response or _openrouter_success_response()),
        )
    )
    return ProviderRouter(registry)


class ProviderAiServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_returns_provider_output(self) -> None:
        service = ProviderRouterAIService(_router_with_fake_transport(), free_only=True)
        await service.start(memory_context="")
        self.assertTrue(service.is_available())
        self.assertEqual(await service.ask("oi"), "JARVIS FREE ROUTER OK")

    async def test_backend_name_reports_provider_and_free_mode(self) -> None:
        service = ProviderRouterAIService(_router_with_fake_transport(), free_only=True)
        self.assertEqual(service.backend_name, "openrouter (free)")
        paid = ProviderRouterAIService(_router_with_fake_transport(), free_only=False)
        self.assertEqual(paid.backend_name, "openrouter")

    async def test_unconfigured_provider_is_not_available(self) -> None:
        service = ProviderRouterAIService(_router_with_fake_transport(api_key=None))
        self.assertFalse(service.is_available())
        self.assertEqual(service.backend_name, "nenhum")

    async def test_history_is_resent_because_the_api_is_stateless(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response())
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=transport))
        service = ProviderRouterAIService(ProviderRouter(registry), free_only=True)

        await service.start(memory_context="")
        await service.ask("primeira")
        await service.ask("segunda")

        messages = transport.calls[-1]["body"]["messages"]
        contents = [m["content"] for m in messages]
        self.assertIn("primeira", contents)
        self.assertIn("JARVIS FREE ROUTER OK", contents)
        self.assertEqual(contents[-1], "segunda")  # o turno atual vai por último

    async def test_empty_response_fails_loudly(self) -> None:
        """Um modelo de raciocínio pode gastar o orçamento inteiro e não
        devolver texto — isso é falha, não uma resposta válida vazia."""
        from services.ai_service import AIServiceUnavailableError

        empty = _openrouter_success_response()
        body = json.loads(empty.body)
        body["choices"][0]["message"]["content"] = ""
        empty = type(empty)(status=200, body=json.dumps(body))

        service = ProviderRouterAIService(_router_with_fake_transport(response=empty), free_only=True)
        await service.start(memory_context="")
        with self.assertRaises(AIServiceUnavailableError):
            await service.ask("oi")

    async def test_paid_response_under_free_only_is_refused(self) -> None:
        """Regra de ouro: `free_only` nunca aceita silenciosamente uma
        resposta que o provider relatou como paga."""
        from services.ai_service import AIServiceUnavailableError

        paid = _openrouter_success_response(model="anthropic/claude-sonnet-5", cost=0.002)
        service = ProviderRouterAIService(_router_with_fake_transport(response=paid), free_only=True)
        await service.start(memory_context="")
        with self.assertRaises(AIServiceUnavailableError):
            await service.ask("oi")

    async def test_metadata_is_captured_outside_the_message_text(self) -> None:
        service = ProviderRouterAIService(_router_with_fake_transport(), free_only=True)
        await service.start(memory_context="")
        reply = await service.ask("oi")

        summary = service.last_result_summary
        self.assertEqual(summary["provider"], "openrouter")
        self.assertEqual(summary["requested_model"], FREE_MODEL)
        self.assertEqual(summary["served_model"], FREE_MODEL)
        self.assertTrue(summary["is_free"])
        # Nada disso contamina o texto que vai para o chat.
        self.assertNotIn("openrouter", reply)
        self.assertNotIn("token", reply.lower())

    async def test_memory_context_reaches_the_provider_as_system_prompt(self) -> None:
        transport = FakeHttpTransport(response=_openrouter_success_response())
        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="sk-or-fake", transport=transport))
        service = ProviderRouterAIService(ProviderRouter(registry), free_only=True)

        await service.start(memory_context="Perfil do usuário:\nO usuário se chama Davi.")
        await service.ask("quem sou eu?")

        system = transport.calls[-1]["body"]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("Davi", system["content"])
        self.assertIn("JARVIS", system["content"])  # identidade de runtime junto


class ApplicationProviderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """O caminho completo: `JarvisApplication.send_message()` até o provider,
    passando por Orchestrator/AIService — o mesmo fluxo de produção."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_send_message_flows_through_provider_router(self) -> None:
        service = ProviderRouterAIService(_router_with_fake_transport(), free_only=True)
        application = build_isolated_application(self.tmp_path, ai_service=service)
        await application.start()
        try:
            response = await application.send_message("oi")

            self.assertEqual(response.status, ResponseStatus.SUCCESS)
            self.assertEqual(response.content, "JARVIS FREE ROUTER OK")
            # A conversa persistiu os dois lados.
            messages = application.get_messages()
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].content, "oi")
        finally:
            await application.stop()

    async def test_provider_failure_keeps_the_user_message(self) -> None:
        """Item 56: falha da IA não pode apagar a mensagem do usuário."""
        from services.providers.openrouter_provider import HttpResponse

        failing = _router_with_fake_transport(response=HttpResponse(status=503, body="{}"))
        service = ProviderRouterAIService(failing, free_only=True)
        application = build_isolated_application(self.tmp_path, ai_service=service)
        await application.start()
        try:
            response = await application.send_message("mensagem importante")

            self.assertEqual(response.status, ResponseStatus.ERROR)
            messages = application.get_messages()
            self.assertGreaterEqual(len(messages), 1)
            self.assertEqual(messages[0].content, "mensagem importante")
        finally:
            await application.stop()

    async def test_app_works_normally_without_any_provider_key(self) -> None:
        """Item 15: sem chave, o JARVIS abre e o chat não quebra."""
        settings = build_isolated_settings(self.tmp_path)
        service = create_ai_service(settings)  # nenhuma env var de chave nos testes

        self.assertFalse(service.is_available())

        application = build_isolated_application(self.tmp_path, ai_service=service)
        await application.start()
        try:
            response = await application.send_message("oi")
            self.assertEqual(response.status, ResponseStatus.ERROR)
            self.assertEqual(response.error.code.value, "ai_unavailable")
            # E a mensagem do usuário continua no histórico.
            self.assertEqual(application.get_messages()[0].content, "oi")
        finally:
            await application.stop()


if __name__ == "__main__":
    unittest.main()
