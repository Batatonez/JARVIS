"""Fakes/dublês usados pelos testes de IA e voz — nenhum faz chamada real a
nenhuma API, microfone, engine de TTS ou processo externo (Claude Agent SDK,
Anthropic, Vosk, SAPI, ou qualquer outro).
"""

import asyncio

from services.ai_service import AIService
from services.stt_service import STTStatus, STTUnavailableError, SpeechToTextService
from services.tts_service import TTSUnavailableError, TextToSpeechService


class FakeAIService(AIService):
    """AIService genérico e controlável por teste — usado para provar que o
    Orchestrator/Core/Application lidam com qualquer AIService (não conhecem
    Claude). `delay` simula uma chamada lenta, para testes de concorrência e
    cancelamento (`await asyncio.sleep(delay)` antes de responder)."""

    def __init__(
        self,
        *,
        available: bool = True,
        reply: str = "ok",
        ask_error: Exception | None = None,
        delay: float = 0.0,
    ):
        self._available = available
        self._reply = reply
        self._ask_error = ask_error
        self._delay = delay
        self._session_active = False
        self.started = False
        self.closed = False
        self.start_count = 0
        self.close_count = 0
        self.received_memory_context: str | None = None
        self.received_preferences = None
        self.asked_messages: list[str] = []

    def is_available(self) -> bool:
        return self._available

    @property
    def session_active(self) -> bool:
        return self._session_active

    @property
    def backend_name(self) -> str:
        return "Fake"

    async def start(self, *, memory_context: str = "", preferences=None) -> None:
        self.started = True
        self.start_count += 1
        self.received_memory_context = memory_context
        # v1.6.0 — guardado para os testes poderem afirmar que as preferências
        # de idioma/região chegaram ao serviço de IA.
        self.received_preferences = preferences
        self._session_active = True

    async def ask(self, message: str) -> str:
        self.asked_messages.append(message)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._ask_error:
            raise self._ask_error
        return self._reply

    async def close(self) -> None:
        self.closed = True
        self.close_count += 1
        self._session_active = False


class FakeSTTService(SpeechToTextService):
    """SpeechToTextService controlável por teste — nunca abre um microfone
    real. `on_level`, quando passado a `start_listening`, recebe um valor de
    teste uma única vez (simula o throttling real do provider)."""

    def __init__(
        self,
        *,
        available: bool = True,
        microphone: bool = True,
        status: STTStatus | None = None,
        transcript: str = "",
        fail_transcription: bool = False,
        delay: float = 0.0,
    ) -> None:
        self._available = available
        self._microphone = microphone
        if status is not None:
            self._status = status
        elif not available:
            self._status = STTStatus.SETUP_REQUIRED
        elif not microphone:
            self._status = STTStatus.NO_MICROPHONE
        else:
            self._status = STTStatus.READY
        self._transcript = transcript
        self._fail_transcription = fail_transcription
        self._delay = delay
        self.listening = False
        self.start_calls = 0
        self.cancel_calls = 0
        self.last_level: float | None = None

    def is_available(self) -> bool:
        return self._available

    @property
    def status(self) -> STTStatus:
        return self._status

    @property
    def microphone_available(self) -> bool:
        return self._microphone

    async def start_listening(self, *, on_level=None) -> None:
        if not (self._available and self._microphone):
            raise STTUnavailableError("Microfone ou reconhecimento de fala não está disponível.")
        self.start_calls += 1
        self.listening = True
        if on_level is not None:
            self.last_level = 0.5
            on_level(self.last_level)

    async def stop_and_transcribe(self) -> str:
        if not self.listening:
            raise STTUnavailableError("Nenhuma captura de áudio em andamento.")
        self.listening = False
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail_transcription:
            raise STTUnavailableError("Falha simulada de transcrição.")
        return self._transcript

    async def cancel(self) -> None:
        self.listening = False
        self.cancel_calls += 1


class FakeTTSService(TextToSpeechService):
    """TextToSpeechService controlável por teste — nunca toca um engine de
    voz real (SAPI/pyttsx3)."""

    def __init__(self, *, available: bool = True, fail: bool = False, delay: float = 0.0) -> None:
        self._available = available
        self._fail = fail
        self._delay = delay
        self.spoken: list[str] = []
        self.stop_calls = 0
        self._stop_event = asyncio.Event()

    def is_available(self) -> bool:
        return self._available

    async def speak(self, text: str) -> None:
        if not self._available:
            raise TTSUnavailableError("Síntese de voz não está disponível.")
        if self._fail:
            raise TTSUnavailableError("Falha simulada de síntese de voz.")
        self._stop_event = asyncio.Event()
        if self._delay:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._delay)
                return  # stop() interrompeu antes do "tempo de fala" terminar
            except asyncio.TimeoutError:
                pass
        self.spoken.append(text)

    async def stop(self) -> None:
        self.stop_calls += 1
        self._stop_event.set()


class FakeClaudeSDKClient:
    """Substitui `claude_agent_sdk.ClaudeSDKClient` nos testes do
    ClaudeAgentProvider. `responses` é uma lista de listas de mensagens: uma
    lista de mensagens por chamada a `query()`, na ordem em que ocorrerem."""

    def __init__(
        self,
        *,
        responses: list[list[object]] | None = None,
        connect_error: Exception | None = None,
        ask_error: Exception | None = None,
    ):
        self._responses = list(responses or [])
        self._connect_error = connect_error
        self._ask_error = ask_error
        self.connected = False
        self.disconnected = False
        self.queries: list[str] = []

    async def connect(self, prompt=None) -> None:
        if self._connect_error:
            raise self._connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries.append(prompt)
        if self._ask_error:
            raise self._ask_error

    async def receive_response(self):
        messages = self._responses.pop(0) if self._responses else []
        for message in messages:
            yield message
