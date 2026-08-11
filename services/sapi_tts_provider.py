"""`SapiTTSProvider` — TTS offline via SAPI5 do Windows, usando `pyttsx3`
(https://github.com/nateshmbhat/pyttsx3) como wrapper. Único módulo que
importa `pyttsx3` (ver `services/tts_service.py` para o porquê).

Nenhum modelo é baixado: as vozes vêm já instaladas no Windows. Escolhe uma
voz pt-BR automaticamente se o sistema tiver uma instalada; senão usa a voz
padrão do sistema — nunca falha por falta de português. Não tenta imitar
nenhuma voz de ator/personagem, e não há clonagem de voz.

Cada chamada a `speak()` cria um `pyttsx3.Engine` novo dentro de um executor
(evita reusar o mesmo engine entre threads, historicamente frágil no driver
SAPI5 do pyttsx3) e guarda uma referência para `stop()` conseguir interromper
a fala em andamento a partir da thread do event loop.
"""

import asyncio
import logging
import threading

import pyttsx3

from services.tts_service import TextToSpeechService

logger = logging.getLogger(__name__)


class SapiTTSProvider(TextToSpeechService):
    def __init__(self, *, voice_name: str | None = None) -> None:
        # Engine descartável só para validar que o SAPI5 responde e resolver
        # a voz a usar — falha rápido aqui se o TTS realmente não funcionar
        # neste ambiente (cai para UnavailableTTSService via create_tts_service).
        probe = pyttsx3.init()
        try:
            self._voice_id = self._resolve_voice(probe, voice_name)
        finally:
            probe.stop()

        self._engine: "pyttsx3.Engine | None" = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @staticmethod
    def _resolve_voice(engine: "pyttsx3.Engine", voice_name: str | None) -> str | None:
        voices = engine.getProperty("voices") or []
        if not voices:
            return None
        if voice_name:
            for voice in voices:
                if voice_name.lower() in (voice.name or "").lower():
                    return voice.id
        for voice in voices:
            id_lower = (voice.id or "").lower()
            languages = getattr(voice, "languages", None) or []
            lang_has_pt = any("pt" in (lang.decode(errors="ignore") if isinstance(lang, bytes) else str(lang)).lower() for lang in languages)
            if "pt" in id_lower or "portuguese" in id_lower or "brazil" in id_lower or lang_has_pt:
                return voice.id
        return voices[0].id

    def is_available(self) -> bool:
        return True

    async def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        loop = asyncio.get_running_loop()
        self._stop_event.clear()
        await loop.run_in_executor(None, self._speak_blocking, text)

    def _speak_blocking(self, text: str) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            engine = pyttsx3.init()
            self._engine = engine
            try:
                if self._voice_id:
                    engine.setProperty("voice", self._voice_id)
                engine.say(text)
                engine.runAndWait()
            except Exception:
                logger.exception("Falha ao sintetizar/reproduzir fala.")
            finally:
                self._engine = None

    async def stop(self) -> None:
        self._stop_event.set()
        engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                logger.debug("Falha ao interromper o engine de TTS (pode já ter terminado).")
