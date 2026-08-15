"""`VoiceService`: coordena STT + TTS para o JARVIS — captura por
push-to-talk, transcrição, fala e cancelamento. Não conhece Qt/QML nem
`JarvisCore`/`Orchestrator`: só fala com `SpeechToTextService`/
`TextToSpeechService` e emite eventos no `EventBus` interno (o mesmo já
usado por `JarvisCore`). Quem fala com isto é só `JarvisApplication` (ver
docs/architecture.md, seção Voice Foundation).

Eventos emitidos (relayed por `JarvisApplication` como `AppEvent`, igual a
`state.changed`/`ai.connected`):

    voice.listening.started / voice.listening.stopped
    voice.transcription.started / .completed / .failed
    voice.speaking.started / .stopped / .failed
    voice.level   (payload: {"level": float 0.0-1.0}, throttled na origem)
"""

import logging

from app.models import TranscriptionResult
from services.event_bus import EventBus
from services.speech_sanitizer import sanitize_text_for_tts
from services.stt_service import SpeechToTextService, STTStatus, STTUnavailableError, create_stt_service
from services.tts_service import TextToSpeechService, TTSUnavailableError, create_tts_service

logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(
        self,
        settings,
        event_bus: EventBus,
        *,
        stt: SpeechToTextService | None = None,
        tts: TextToSpeechService | None = None,
    ) -> None:
        self._event_bus = event_bus
        self.stt = stt or create_stt_service(settings)
        self.tts = tts or create_tts_service(settings)
        self.voice_output_enabled = settings.voice_output_enabled
        self._listening = False
        self._speaking = False

    # ------------------------------------------------------------------
    # Disponibilidade (dados reais, nunca inventados)
    # ------------------------------------------------------------------

    @property
    def microphone_available(self) -> bool:
        return self.stt.microphone_available

    @property
    def stt_ready(self) -> bool:
        return self.stt.is_available()

    @property
    def stt_status(self) -> STTStatus:
        """Diagnóstico real (`STTStatus`) para o HUD decidir entre mostrar
        `SETUP_REQUIRED` (oferecer instalar o modelo), `NO_MICROPHONE`,
        `READY` ou `UNAVAILABLE` — nunca um "indisponível" genérico."""
        return self.stt.status

    @property
    def tts_ready(self) -> bool:
        return self.tts.is_available()

    @property
    def voice_available(self) -> bool:
        """Pronto para push-to-talk: engine de STT carregado E microfone presente."""
        return self.stt_ready and self.microphone_available

    @property
    def listening(self) -> bool:
        return self._listening

    @property
    def speaking(self) -> bool:
        return self._speaking

    # ------------------------------------------------------------------
    # Entrada de voz (push-to-talk)
    # ------------------------------------------------------------------

    async def start_listening(self) -> None:
        if not self.voice_available:
            raise STTUnavailableError("Microfone ou reconhecimento de fala não está disponível.")
        if self._listening:
            return

        def _on_level(level: float) -> None:
            self._event_bus.emit("voice.level", level=level)

        await self.stt.start_listening(on_level=_on_level)
        self._listening = True
        self._event_bus.emit("voice.listening.started")

    async def stop_and_transcribe(self) -> TranscriptionResult:
        if not self._listening:
            raise STTUnavailableError("Nenhuma captura de áudio em andamento.")
        self._listening = False
        self._event_bus.emit("voice.listening.stopped")
        self._event_bus.emit("voice.transcription.started")
        try:
            text = await self.stt.stop_and_transcribe()
        except Exception as exc:
            logger.warning("Falha na transcrição de voz: %s", exc)
            self._event_bus.emit("voice.transcription.failed", error=str(exc))
            raise
        self._event_bus.emit("voice.transcription.completed", text=text)
        return TranscriptionResult(text=text)

    async def cancel_listening(self) -> None:
        if not self._listening:
            return
        self._listening = False
        await self.stt.cancel()
        self._event_bus.emit("voice.listening.stopped")

    # ------------------------------------------------------------------
    # Saída de voz
    # ------------------------------------------------------------------

    async def speak(self, text: str) -> None:
        """Fala um texto. A sanitização para TTS acontece AQUI, e não em cada
        chamador (v1.6.0).

        Este método é o ponto único por onde passam a fala automática da
        resposta e o replay/listen manual. Sanitizar aqui torna impossível,
        por construção, existir um caminho que entregue Markdown cru ao
        sintetizador — que era o bug: `**Importante**` sendo lido como
        "asterisco asterisco importante asterisco asterisco". Deixar a
        responsabilidade nos chamadores só garantiria que o próximo caminho
        novo esquecesse.

        O texto original nunca é alterado: o que é falado é uma derivação
        descartável dele. Chat, banco e Copy continuam com a formatação."""
        if not self.tts_ready:
            raise TTSUnavailableError("Síntese de voz não está disponível.")

        speech_text = sanitize_text_for_tts(text)
        if not speech_text:
            # Sobrou nada pronunciável (ex.: a resposta era só um bloco de
            # código). Não é erro — simplesmente não há o que falar, e forçar
            # o sintetizador com string vazia produziria um evento de fala
            # que nunca termina em som nenhum.
            logger.info("Nada pronunciável na resposta após sanitização; fala ignorada.")
            return

        self._speaking = True
        self._event_bus.emit("voice.speaking.started")
        try:
            await self.tts.speak(speech_text)
        except Exception as exc:
            logger.warning("Falha ao sintetizar fala: %s", exc)
            self._event_bus.emit("voice.speaking.failed", error=str(exc))
            raise
        else:
            self._event_bus.emit("voice.speaking.stopped")
        finally:
            self._speaking = False

    async def stop_speaking(self) -> None:
        if not self._speaking:
            return
        await self.tts.stop()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        await self.cancel_listening()
        await self.stop_speaking()
