"""Testes do JarvisBridge — offline, sem chamada real de IA.

Usa `JarvisApplication` real por cima de `FakeAIService` (mesmos fakes da
Application Layer), exercitando o caminho de verdade: evento interno ->
`JarvisApplication.events()` -> `Bridge._consume_events()` -> Properties Qt.
Não abre janela nem carrega QML (isso é `test_qml_smoke.py`).

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
from frontend.bridge import JarvisBridge
from frontend.message_model import MessageRoles
from services.voice_service import VoiceService
from tests.fakes import FakeAIService, FakeSTTService, FakeTTSService
from tests.helpers import build_isolated_application, build_isolated_core


def _ensure_qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


async def _settle() -> None:
    """Dá ao `_consume_events()` (rodando em background) algumas voltas do
    event loop para drenar a fila antes de checar o estado do Bridge."""
    for _ in range(5):
        await asyncio.sleep(0)


class BridgeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _bridge(self, ai_service=None, dev_mode: bool = False) -> JarvisBridge:
        application = build_isolated_application(Path(self._tmp.name), ai_service=ai_service)
        return JarvisBridge(application, dev_mode=dev_mode)

    def test_dev_mode_defaults_to_false(self) -> None:
        application = build_isolated_application(Path(self._tmp.name))
        bridge = JarvisBridge(application)

        self.assertFalse(bridge.devMode)

    def test_initial_properties_before_start(self) -> None:
        bridge = self._bridge()

        self.assertEqual(bridge.jarvisState, "idle")
        self.assertFalse(bridge.running)
        self.assertFalse(bridge.busy)
        self.assertIsNone(bridge.pendingPermission)
        self.assertEqual(bridge.messages.rowCount(), 0)

    async def test_start_marks_running_and_reflects_ai_status(self) -> None:
        bridge = self._bridge()

        await bridge.start()
        try:
            self.assertTrue(bridge.running)
            self.assertFalse(bridge.aiConfigured)
            self.assertEqual(bridge.aiBackend, "nenhum")
            self.assertFalse(bridge.aiSessionActive)
        finally:
            await bridge._shutdown()

    async def test_start_with_available_ai_reflects_backend(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        bridge = self._bridge(ai_service=fake_ai)

        await bridge.start()
        try:
            self.assertTrue(bridge.aiConfigured)
            self.assertEqual(bridge.aiBackend, "Fake")
            self.assertTrue(bridge.aiSessionActive)
        finally:
            await bridge._shutdown()

    async def test_shutdown_is_clean_and_marks_can_close(self) -> None:
        bridge = self._bridge()
        await bridge.start()

        await bridge._shutdown()

        self.assertTrue(bridge.canClose)
        self.assertFalse(bridge.running)
        self.assertTrue(bridge._event_task is None or bridge._event_task.done())


class BridgeEventFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _bridge(self, ai_service=None) -> JarvisBridge:
        application = build_isolated_application(Path(self._tmp.name), ai_service=ai_service)
        return JarvisBridge(application, dev_mode=True)

    async def test_state_changed_flows_from_backend_to_property(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok", delay=0.02)
        bridge = self._bridge(ai_service=fake_ai)
        await bridge.start()
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
        bridge = self._bridge(ai_service=fake_ai)
        await bridge.start()
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
        bridge = self._bridge(ai_service=fake_ai)
        await bridge.start()
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
        bridge = self._bridge(ai_service=fake_ai)
        await bridge.start()
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

    async def test_new_conversation_clears_message_model(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        bridge = self._bridge(ai_service=fake_ai)
        await bridge.start()
        try:
            await bridge._app.send_message("mensagem antiga")
            await _settle()
            self.assertGreater(bridge.messages.rowCount(), 0)

            bridge.newConversation()
            for _ in range(30):
                if bridge.messages.rowCount() == 0:
                    break
                await asyncio.sleep(0.005)

            self.assertEqual(bridge.messages.rowCount(), 0)
        finally:
            await bridge._shutdown()

    async def test_permission_requested_and_resolved_flow(self) -> None:
        bridge = self._bridge()
        await bridge.start()
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
        bridge = self._bridge()
        await bridge.start()
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


class BridgeDevModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_simulate_state_is_noop_outside_dev_mode(self) -> None:
        application = build_isolated_application(Path(self._tmp.name))
        bridge = JarvisBridge(application, dev_mode=False)
        await bridge.start()
        try:
            bridge.simulateState("error")
            self.assertEqual(bridge.jarvisState, "idle")
        finally:
            await bridge._shutdown()

    async def test_simulate_state_works_in_dev_mode(self) -> None:
        application = build_isolated_application(Path(self._tmp.name))
        bridge = JarvisBridge(application, dev_mode=True)
        await bridge.start()
        try:
            bridge.simulateState("error")
            self.assertEqual(bridge.jarvisState, "error")

            bridge.simulateState("permission")
            await _settle()
            self.assertIsNotNone(bridge.pendingPermission)
        finally:
            await bridge._shutdown()

    async def test_simulate_state_supports_processing_speech(self) -> None:
        application = build_isolated_application(Path(self._tmp.name))
        bridge = JarvisBridge(application, dev_mode=True)
        await bridge.start()
        try:
            bridge.simulateState("processing_speech")
            self.assertEqual(bridge.jarvisState, "processing_speech")
        finally:
            await bridge._shutdown()


class BridgeVoiceTests(unittest.IsolatedAsyncioTestCase):
    """Fluxo de voz pelos slots/properties reais do Bridge — nunca toca
    microfone/TTS real (FakeSTTService/FakeTTSService)."""

    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _bridge(self, *, stt=None, tts=None, dev_mode: bool = False) -> JarvisBridge:
        core = build_isolated_core(Path(self._tmp.name))
        voice = VoiceService(
            core.settings,
            core.event_bus,
            stt=stt if stt is not None else FakeSTTService(),
            tts=tts if tts is not None else FakeTTSService(),
        )
        from app.application import JarvisApplication

        application = JarvisApplication(core, voice_service=voice)
        return JarvisBridge(application, dev_mode=dev_mode)

    async def test_microphone_available_and_stt_ready_reflect_status(self) -> None:
        bridge = self._bridge(stt=FakeSTTService(available=True, microphone=False))
        await bridge.start()
        try:
            self.assertTrue(bridge.sttReady)
            self.assertFalse(bridge.microphoneAvailable)
            self.assertFalse(bridge.voiceAvailable)
        finally:
            await bridge._shutdown()

    async def test_toggle_listening_round_trip_via_real_slots(self) -> None:
        stt = FakeSTTService(transcript="ligar as luzes")
        bridge = self._bridge(stt=stt)
        await bridge.start()
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
        bridge = self._bridge(tts=tts)
        await bridge.start()
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


class BridgeStreamingPrepTests(unittest.IsolatedAsyncioTestCase):
    """`response.delta` não existe no backend real ainda (sem Claude
    conectado) — estes testes simulam o evento diretamente para confirmar
    que o Bridge/MessageListModel já sabem reagir quando ele existir de
    verdade (v0.8: preparação, não streaming implementado)."""

    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_response_delta_updates_existing_message_progressively(self) -> None:
        fake_ai = FakeAIService(available=True, reply="resposta inicial")
        bridge = JarvisBridge(build_isolated_application(Path(self._tmp.name), ai_service=fake_ai))
        await bridge.start()
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
        bridge = JarvisBridge(build_isolated_application(Path(self._tmp.name)))
        await bridge.start()
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
