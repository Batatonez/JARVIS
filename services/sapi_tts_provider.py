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

    # Pistas por idioma para casar com o nome/id da voz instalada no Windows.
    # Uma lista por idioma (e não uma regex) porque os nomes variam muito
    # entre versões do Windows: "Microsoft Maria Desktop - Portuguese(Brazil)",
    # "Microsoft Helena Desktop - Spanish (Spain)", etc.
    _VOICE_HINTS: dict[str, tuple[str, ...]] = {
        "pt-BR": ("pt-br", "pt_br", "portuguese", "brazil", "brasil"),
        "en-US": ("en-us", "en_us", "english", "united states"),
        "es-ES": ("es-es", "es_es", "spanish", "espa"),
    }

    def select_language(self, locale_tag: str) -> bool:
        """Troca para uma voz do idioma pedido, se houver uma instalada
        (v1.6.0). Devolve `True` quando trocou.

        Nunca baixa voz e nunca levanta: sem voz correspondente, mantém a
        atual e devolve `False`. Um idioma sem voz instalada é uma limitação
        do sistema do usuário, não um erro do JARVIS — e falar com sotaque
        errado é melhor do que não falar."""
        hints = self._VOICE_HINTS.get(locale_tag) or self._VOICE_HINTS.get(
            (locale_tag or "").split("-")[0]
        )
        if not hints:
            return False
        try:
            probe = pyttsx3.init()
            try:
                voices = probe.getProperty("voices") or []
            finally:
                probe.stop()
        except Exception:
            logger.info("Não foi possível listar vozes do sistema; mantendo a voz atual.")
            return False

        for voice in voices:
            haystack = f"{voice.id or ''} {getattr(voice, 'name', '') or ''}".lower()
            if any(hint in haystack for hint in hints):
                self._voice_id = voice.id
                logger.info("Voz do TTS ajustada para o idioma %s.", locale_tag)
                return True
        logger.info("Nenhuma voz instalada para %s; mantendo a voz atual.", locale_tag)
        return False

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
