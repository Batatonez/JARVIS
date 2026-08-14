"""Horário das mensagens (v1.2) — persistência em UTC, exibição em local.

O bug corrigido: o modelo formatava `timestamp.strftime("%H:%M")` direto
sobre um datetime UTC, então uma mensagem enviada às 21:11 em UTC-3
aparecia como 00:11 no chat.

Estes testes fixam um fuso explícito em vez de depender do fuso da máquina
que roda a suíte — senão passariam por acidente em UTC e falhariam em
qualquer outro lugar.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import Message, MessageRole
from frontend.message_model import MessageListModel, MessageRoles, to_local_display_time
from services.ai_service import UnavailableAIService
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_account_manager, build_isolated_voice_service

# Fusos de teste — deliberadamente diferentes entre si e de UTC.
SAO_PAULO = timezone(timedelta(hours=-3))
TOKYO = timezone(timedelta(hours=9))
KATHMANDU = timezone(timedelta(hours=5, minutes=45))  # offset quebrado, pega erro de hora inteira


async def _settle() -> None:
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0)


class LocalDisplayTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        # 2026-08-14 00:11 UTC == 13/08 21:11 em UTC-3
        self.moment = datetime(2026, 8, 14, 0, 11, tzinfo=timezone.utc)

    def test_utc_moment_is_converted_to_local_offset(self) -> None:
        self.assertEqual(to_local_display_time(self.moment, tz=SAO_PAULO), "21:11")

    def test_same_moment_renders_differently_in_another_timezone(self) -> None:
        """Prova que a conversão é real e não um offset fixo no código."""
        self.assertEqual(to_local_display_time(self.moment, tz=TOKYO), "09:11")
        self.assertEqual(to_local_display_time(self.moment, tz=timezone.utc), "00:11")

    def test_fractional_offset_timezone(self) -> None:
        self.assertEqual(to_local_display_time(self.moment, tz=KATHMANDU), "05:56")

    def test_naive_datetime_is_treated_as_utc_not_as_local(self) -> None:
        """Registro antigo sem offset: assumir "local" deslocaria o horário
        silenciosamente. A convenção de armazenamento é UTC."""
        naive = datetime(2026, 8, 14, 0, 11)
        self.assertEqual(to_local_display_time(naive, tz=SAO_PAULO), "21:11")

    def test_conversion_can_cross_the_day_boundary(self) -> None:
        # 00:11 UTC ainda é 13/08 em UTC-3 — o horário exibido é do dia anterior.
        self.assertEqual(to_local_display_time(self.moment, tz=SAO_PAULO), "21:11")
        # E 23:50 UTC-3 já é o dia seguinte em UTC.
        late = datetime(2026, 8, 14, 2, 50, tzinfo=timezone.utc)
        self.assertEqual(to_local_display_time(late, tz=SAO_PAULO), "23:50")

    def test_system_local_conversion_matches_python_astimezone(self) -> None:
        """Sem `tz`, usa o fuso do sistema — o mesmo que `astimezone()`."""
        expected = self.moment.astimezone().strftime("%H:%M")
        self.assertEqual(to_local_display_time(self.moment), expected)


class MessageModelTimestampTests(unittest.TestCase):
    def test_model_exposes_local_time_not_utc(self) -> None:
        moment = datetime(2026, 8, 14, 0, 11, tzinfo=timezone.utc)
        model = MessageListModel()
        model.sync([Message(role=MessageRole.USER, content="oi", timestamp=moment)])

        shown = model.data(model.index(0, 0), MessageRoles.TimestampRole)

        self.assertEqual(shown, moment.astimezone().strftime("%H:%M"))
        # E, num sistema fora de UTC, precisa ser diferente do horário UTC.
        if moment.astimezone().utcoffset() != timedelta(0):
            self.assertNotEqual(shown, "00:11")


class TimestampPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """A persistência não pode virar local — o banco guarda UTC."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _account(self):
        return build_isolated_account_manager(
            self.tmp_path,
            ai_service_factory=lambda: FakeAIService(available=True, reply="ok"),
            voice_service_factory=build_isolated_voice_service,
        )

    async def test_stored_timestamp_is_utc_and_offset_aware(self) -> None:
        account = self._account()
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("oi")
            await _settle()

            connection = sqlite3.connect(str(account.settings.db_path))
            try:
                raw = connection.execute("SELECT timestamp FROM messages LIMIT 1").fetchone()[0]
            finally:
                connection.close()

            stored = datetime.fromisoformat(raw)
            self.assertIsNotNone(stored.tzinfo, "timestamp precisa ser gravado com offset")
            self.assertEqual(stored.utcoffset(), timedelta(0), "banco deve guardar UTC")
        finally:
            await account.shutdown()

    async def test_timestamp_survives_reload_as_aware_datetime(self) -> None:
        account = self._account()
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("oi")
            await _settle()

            reloaded = account._conversations.get_conversation(account.current_conversation_id, user.id)

            for message in reloaded:
                self.assertIsNotNone(message.timestamp.tzinfo)
                # E o modelo consegue exibir sem quebrar.
                self.assertRegex(to_local_display_time(message.timestamp), r"^\d{2}:\d{2}$")
        finally:
            await account.shutdown()

    async def test_old_message_and_new_message_both_display_correctly(self) -> None:
        """Mensagem antiga (gravada há dias) e recém-criada seguem a mesma
        regra de conversão."""
        account = self._account()
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            conversation = await account.start_new_conversation()

            old_moment = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
            account._conversations.save_message(
                conversation,
                user.id,
                Message(role=MessageRole.USER, content="antiga", timestamp=old_moment),
            )
            await account.app.send_message("nova")
            await _settle()

            messages = account._conversations.get_conversation(conversation, user.id)
            model = MessageListModel()
            model.sync(messages)

            shown_old = model.data(model.index(0, 0), MessageRoles.TimestampRole)
            self.assertEqual(shown_old, old_moment.astimezone().strftime("%H:%M"))
            for row in range(model.rowCount()):
                self.assertRegex(
                    model.data(model.index(row, 0), MessageRoles.TimestampRole), r"^\d{2}:\d{2}$"
                )
        finally:
            await account.shutdown()


class ConversationTimestampTests(unittest.IsolatedAsyncioTestCase):
    """A sidebar agrupa por data usando `updatedAt` — o ISO precisa carregar
    o offset, senão o JS do QML interpreta como local e o agrupamento
    (Hoje/Ontem) erra perto da meia-noite."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_conversation_summary_timestamps_are_offset_aware(self) -> None:
        account = build_isolated_account_manager(
            self.tmp_path,
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
        )
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.start_new_conversation()

            summary = account.list_conversations()[0]

            self.assertIsNotNone(summary.created_at.tzinfo)
            self.assertIsNotNone(summary.updated_at.tzinfo)
            # O ISO enviado ao QML precisa terminar com offset explícito.
            self.assertRegex(summary.updated_at.isoformat(), r"(\+|-)\d{2}:\d{2}$")
        finally:
            await account.shutdown()


if __name__ == "__main__":
    unittest.main()
