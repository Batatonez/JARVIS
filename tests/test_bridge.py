"""Testes do JarvisBridge — offline, sem chamada real de IA.

v0.9: o Bridge é auth-first (ver frontend/bridge.py) — `self._app` só existe
depois de um login/registro bem-sucedido via `AccountManager`. Os testes
sempre chamam `bridge.initialize()` (tenta auto-login, não encontra sessão)
seguido de `bridge._register(...)`/`bridge._login(...)` (as corrotinas por
trás dos slots públicos) antes de exercitar qualquer coisa que dependa de
`bridge._app`.

Usa `QGuiApplication`, não `QCoreApplication`: como o singleton do Qt é
compartilhado por todo o processo de teste, se este módulo criasse um
`QCoreApplication` "puro" primeiro, `test_qml_smoke.py` (que roda depois,
em ordem alfabética) reaproveitaria essa instância via
`QGuiApplication.instance()` sem nunca construir uma `QGuiApplication` de
verdade — e carregar QML sem isso trava. `QGuiApplication` é superset de
`QCoreApplication`, então isso não muda nada do comportamento testado aqui.
"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from app.models import AppEvent, PermissionStatus, RiskLevel
from frontend.message_model import MessageRoles
from services.voice_service import VoiceService
from tests.fakes import FakeAIService, FakeSTTService, FakeTTSService
from tests.helpers import build_isolated_bridge, build_isolated_core, build_isolated_voice_service


def _ensure_qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


async def _settle() -> None:
    """Dá ao `_consume_events()` (rodando em background) algumas voltas do
    event loop para drenar a fila antes de checar o estado do Bridge."""
    for _ in range(5):
        await asyncio.sleep(0)


class _BridgeTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _bridge(self, *, ai_service=None, voice_service_factory=None, dev_mode: bool = False):
        ai_service_factory = (lambda: ai_service) if ai_service is not None else None
        return build_isolated_bridge(
            self.tmp_path,
            dev_mode=dev_mode,
            ai_service_factory=ai_service_factory,
            voice_service_factory=voice_service_factory or build_isolated_voice_service,
        )

    async def _bridge_with_session(self, *, ai_service=None, voice_service_factory=None, dev_mode: bool = False):
        bridge = self._bridge(ai_service=ai_service, voice_service_factory=voice_service_factory, dev_mode=dev_mode)
        await bridge.initialize()
        await bridge._register("alice", "Alice", "alice@example.com", "senha-forte-123")
        return bridge


class BridgeLifecycleTests(_BridgeTestCase):
    async def test_dev_mode_defaults_to_false(self) -> None:
        bridge = self._bridge()
        self.assertFalse(bridge.devMode)

    async def test_initial_properties_before_login(self) -> None:
        bridge = self._bridge()
        await bridge.initialize()  # sem sessão local -> permanece deslogado

        self.assertFalse(bridge.authenticated)
        self.assertIsNone(bridge.currentUser)
        self.assertEqual(bridge.jarvisState, "idle")
        self.assertFalse(bridge.running)
        self.assertFalse(bridge.busy)
        self.assertIsNone(bridge.pendingPermission)
        self.assertEqual(bridge.messages.rowCount(), 0)

    async def test_register_authenticates_and_reflects_ai_status(self) -> None:
        bridge = await self._bridge_with_session()
        try:
            self.assertTrue(bridge.authenticated)
            self.assertEqual(bridge.currentUser["username"], "alice")
            self.assertTrue(bridge.running)
            self.assertFalse(bridge.aiConfigured)
            self.assertEqual(bridge.aiBackend, "nenhum")
            self.assertFalse(bridge.aiSessionActive)
        finally:
            await bridge._shutdown()

    async def test_register_with_available_ai_reflects_backend(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            self.assertTrue(bridge.aiConfigured)
            self.assertEqual(bridge.aiBackend, "Fake")
            self.assertTrue(bridge.aiSessionActive)
        finally:
            await bridge._shutdown()

    async def test_shutdown_is_clean_and_marks_can_close(self) -> None:
        bridge = await self._bridge_with_session()

        await bridge._shutdown()

        self.assertTrue(bridge.canClose)
        self.assertFalse(bridge.running)
        self.assertTrue(bridge._event_task is None or bridge._event_task.done())


class BridgeAccountTests(_BridgeTestCase):
    """Slots/sinais de conta (v0.9) — o resto dos cenários de auth (hashing,
    isolamento, expiração de sessão etc.) já é coberto em
    `test_account_manager_auth.py` diretamente sobre o `AccountManager`."""

    async def test_register_via_public_slot_authenticates(self) -> None:
        bridge = self._bridge()
        await bridge.initialize()
        try:
            bridge.register("alice", "Alice", "alice@example.com", "senha-forte-123")
            for _ in range(30):
                if bridge.authenticated:
                    break
                await asyncio.sleep(0.005)

            self.assertTrue(bridge.authenticated)
            self.assertEqual(bridge.currentUser["username"], "alice")
        finally:
            await bridge._shutdown()

    async def test_register_duplicate_username_emits_auth_error(self) -> None:
        # `bridge._register()` é a corrotina por trás do slot público — ela
        # nunca deixa a exceção escapar (isso quebraria o event loop do Qt);
        # em vez disso, traduz para `authErrorRaised` (ver frontend/bridge.py).
        bridge = self._bridge()
        await bridge.initialize()
        try:
            await bridge._register("alice", "Alice", "alice@example.com", "senha-forte-123")

            received: list[str] = []
            bridge.authErrorRaised.connect(lambda message: received.append(message))

            await bridge._register("alice", "Outra Alice", "outra@example.com", "outra-senha-456")

            self.assertEqual(len(received), 1)
            self.assertEqual(bridge.currentUser["displayName"], "Alice")  # sessão original preservada
        finally:
            await bridge._shutdown()

    async def test_login_wrong_password_emits_auth_error_via_public_slot(self) -> None:
        bridge = self._bridge()
        await bridge.initialize()
        try:
            await bridge._register("alice", "Alice", "alice@example.com", "senha-forte-123")
            await bridge._logout()
            self.assertFalse(bridge.authenticated)

            received: list[str] = []
            bridge.authErrorRaised.connect(lambda message: received.append(message))

            bridge.login("alice", "senha-errada")
            for _ in range(30):
                if received:
                    break
                await asyncio.sleep(0.005)

            self.assertEqual(len(received), 1)
            self.assertFalse(bridge.authenticated)
        finally:
            await bridge._shutdown()

    async def test_logout_clears_authentication_and_messages(self) -> None:
        bridge = await self._bridge_with_session(ai_service=FakeAIService(available=True, reply="ok"))
        try:
            await bridge._app.send_message("oi")
            await _settle()
            self.assertGreater(bridge.messages.rowCount(), 0)

            bridge.logout()
            for _ in range(30):
                if not bridge.authenticated:
                    break
                await asyncio.sleep(0.005)

            self.assertFalse(bridge.authenticated)
            self.assertIsNone(bridge.currentUser)
            self.assertEqual(bridge.messages.rowCount(), 0)
            self.assertEqual(bridge.conversations, [])
        finally:
            await bridge._shutdown()


class BridgeEventFlowTests(_BridgeTestCase):
    async def test_state_changed_flows_from_backend_to_property(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok", delay=0.02)
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            observed_states = []
            bridge.stateChanged.connect(lambda: observed_states.append(bridge.jarvisState))

            task = asyncio.ensure_future(bridge._app.send_message("oi"))
            await asyncio.sleep(0.005)
            await _settle()
            self.assertTrue(bridge.busy)

            await task
            await _settle()

            self.assertIn("thinking", observed_states)
            self.assertEqual(bridge.jarvisState, "idle")
            self.assertFalse(bridge.busy)
        finally:
            await bridge._shutdown()

    async def test_send_message_slot_updates_history(self) -> None:
        fake_ai = FakeAIService(available=True, reply="Olá, humano.")
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            bridge.sendMessage("oi, tudo bem?")
            for _ in range(30):
                if bridge.messages.rowCount() >= 2:
                    break
                await asyncio.sleep(0.005)

            self.assertEqual(bridge.messages.rowCount(), 2)
            self.assertEqual(fake_ai.asked_messages, ["oi, tudo bem?"])
        finally:
            await bridge._shutdown()

    async def test_busy_rejection_emits_signal_without_touching_history(self) -> None:
        fake_ai = FakeAIService(available=True, reply="resposta A", delay=0.05)
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            received = []
            bridge.busyRejected.connect(lambda message: received.append(message))

            task_a = asyncio.ensure_future(bridge._app.send_message("mensagem A"))
            await asyncio.sleep(0.005)

            await bridge._send("mensagem B")

            self.assertEqual(len(received), 1)
            self.assertNotIn("Traceback", received[0])  # mensagem amigável, nunca um erro cru
            await task_a
        finally:
            await bridge._shutdown()

    async def test_cancel_current_request_returns_state_to_idle(self) -> None:
        fake_ai = FakeAIService(available=True, delay=5.0)
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            task = asyncio.ensure_future(bridge._app.send_message("mensagem lenta"))
            await asyncio.sleep(0.01)
            self.assertTrue(bridge.busy)

            bridge.cancelCurrentRequest()
            for _ in range(30):
                if not bridge.busy:
                    break
                await asyncio.sleep(0.01)

            self.assertFalse(bridge.busy)
            self.assertEqual(bridge.jarvisState, "idle")
            await task
        finally:
            await bridge._shutdown()

    async def test_start_new_conversation_clears_message_model(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            await bridge._app.send_message("mensagem antiga")
            await _settle()
            self.assertGreater(bridge.messages.rowCount(), 0)

            bridge.startNewConversation()
            for _ in range(30):
                if bridge.messages.rowCount() == 0:
                    break
                await asyncio.sleep(0.005)

            self.assertEqual(bridge.messages.rowCount(), 0)
        finally:
            await bridge._shutdown()

    async def test_permission_requested_and_resolved_flow(self) -> None:
        bridge = await self._bridge_with_session()
        try:
            request = bridge._app.permissions.request(
                "restart_server", "Reiniciar o servidor", RiskLevel.DANGEROUS
            )
            await _settle()

            self.assertIsNotNone(bridge.pendingPermission)
            self.assertEqual(bridge.pendingPermission["id"], request.id)
            self.assertEqual(bridge.pendingPermission["riskLevel"], "dangerous")

            bridge.approvePermission(request.id)
            await _settle()

            self.assertIsNone(bridge.pendingPermission)
            self.assertEqual(request.status, PermissionStatus.APPROVED)
        finally:
            await bridge._shutdown()

    async def test_deny_permission_resolves_as_denied(self) -> None:
        bridge = await self._bridge_with_session()
        try:
            request = bridge._app.permissions.request(
                "read_file", "Ler um arquivo", RiskLevel.READ
            )
            await _settle()

            bridge.denyPermission(request.id)
            await _settle()

            self.assertIsNone(bridge.pendingPermission)
            self.assertEqual(request.status, PermissionStatus.DENIED)
        finally:
            await bridge._shutdown()


class BridgeDevModeTests(_BridgeTestCase):
    async def test_simulate_state_is_noop_outside_dev_mode(self) -> None:
        bridge = await self._bridge_with_session(dev_mode=False)
        try:
            bridge.simulateState("error")
            self.assertEqual(bridge.jarvisState, "idle")
        finally:
            await bridge._shutdown()

    async def test_simulate_state_works_in_dev_mode(self) -> None:
        bridge = await self._bridge_with_session(dev_mode=True)
        try:
            bridge.simulateState("error")
            self.assertEqual(bridge.jarvisState, "error")

            bridge.simulateState("permission")
            await _settle()
            self.assertIsNotNone(bridge.pendingPermission)
        finally:
            await bridge._shutdown()

    async def test_simulate_state_supports_processing_speech(self) -> None:
        bridge = await self._bridge_with_session(dev_mode=True)
        try:
            bridge.simulateState("processing_speech")
            self.assertEqual(bridge.jarvisState, "processing_speech")
        finally:
            await bridge._shutdown()


class BridgeVoiceTests(_BridgeTestCase):
    """Fluxo de voz pelos slots/properties reais do Bridge — nunca toca
    microfone/TTS real (FakeSTTService/FakeTTSService)."""

    async def _bridge_with_voice(self, *, stt=None, tts=None, dev_mode: bool = False):
        def _voice_factory(core):
            return VoiceService(
                core.settings,
                core.event_bus,
                stt=stt if stt is not None else FakeSTTService(),
                tts=tts if tts is not None else FakeTTSService(),
            )

        bridge = self._bridge(voice_service_factory=_voice_factory, dev_mode=dev_mode)
        await bridge.initialize()
        await bridge._register("alice", "Alice", "alice@example.com", "senha-forte-123")
        return bridge

    async def test_microphone_available_and_stt_ready_reflect_status(self) -> None:
        bridge = await self._bridge_with_voice(stt=FakeSTTService(available=True, microphone=False))
        try:
            self.assertTrue(bridge.sttReady)
            self.assertFalse(bridge.microphoneAvailable)
            self.assertFalse(bridge.voiceAvailable)
            self.assertEqual(bridge.sttStatus, "no_microphone")
        finally:
            await bridge._shutdown()

    async def test_setup_required_status_is_reflected(self) -> None:
        bridge = await self._bridge_with_voice(stt=FakeSTTService(available=False))
        try:
            self.assertEqual(bridge.sttStatus, "setup_required")
        finally:
            await bridge._shutdown()

    async def test_toggle_listening_round_trip_via_real_slots(self) -> None:
        stt = FakeSTTService(transcript="ligar as luzes")
        bridge = await self._bridge_with_voice(stt=stt)
        try:
            received: list[str] = []
            bridge.transcriptionReady.connect(lambda text: received.append(text))

            bridge.toggleListening()
            for _ in range(30):
                if bridge.jarvisState == "listening":
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(bridge.jarvisState, "listening")

            bridge.toggleListening()
            for _ in range(30):
                if received:
                    break
                await asyncio.sleep(0.005)

            self.assertEqual(received, ["ligar as luzes"])
            self.assertEqual(bridge.jarvisState, "idle")
        finally:
            await bridge._shutdown()

    async def test_stop_speaking_slot_interrupts_real_flow(self) -> None:
        tts = FakeTTSService(delay=5.0)
        bridge = await self._bridge_with_voice(tts=tts)
        try:
            asyncio.ensure_future(bridge._app.speak("frase longa"))
            for _ in range(30):
                if bridge.jarvisState == "speaking":
                    break
                await asyncio.sleep(0.005)
            self.assertEqual(bridge.jarvisState, "speaking")

            bridge.stopSpeaking()
            for _ in range(30):
                if bridge.jarvisState == "idle":
                    break
                await asyncio.sleep(0.005)

            self.assertEqual(bridge.jarvisState, "idle")
            self.assertEqual(tts.stop_calls, 1)
        finally:
            await bridge._shutdown()


class BridgeStreamingPrepTests(_BridgeTestCase):
    """`response.delta` não existe no backend real ainda (sem Claude
    conectado) — estes testes simulam o evento diretamente para confirmar
    que o Bridge/MessageListModel já sabem reagir quando ele existir de
    verdade (v0.8: preparação, não streaming implementado)."""

    async def test_response_delta_updates_existing_message_progressively(self) -> None:
        fake_ai = FakeAIService(available=True, reply="resposta inicial")
        bridge = await self._bridge_with_session(ai_service=fake_ai)
        try:
            await bridge._app.send_message("oi")
            await _settle()
            self.assertEqual(bridge.messages.rowCount(), 2)
            message_id = bridge._app.get_messages()[-1].id

            for delta in ("Ol", "Olá", "Olá, hu", "Olá, humano."):
                event = AppEvent(
                    type="response.delta",
                    timestamp=datetime.now(timezone.utc),
                    payload={"message_id": message_id, "content": delta},
                )
                bridge._handle_event(event)

            last_index = bridge.messages.index(1, 0)
            self.assertEqual(bridge.messages.data(last_index, MessageRoles.ContentRole), "Olá, humano.")
            self.assertEqual(bridge.messages.rowCount(), 2)  # não criou linha nova
        finally:
            await bridge._shutdown()

    async def test_response_delta_with_unknown_message_id_is_ignored(self) -> None:
        bridge = await self._bridge_with_session()
        try:
            event = AppEvent(
                type="response.delta",
                timestamp=datetime.now(timezone.utc),
                payload={"message_id": "id-inexistente", "content": "x"},
            )
            bridge._handle_event(event)  # não deve levantar
        finally:
            await bridge._shutdown()


if __name__ == "__main__":
    unittest.main()
