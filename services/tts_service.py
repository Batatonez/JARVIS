"""Abstração de Text-to-Speech (TTS) do JARVIS.

    TextToSpeechService
        |
        UnavailableTTSService   (sem engine disponível)
        SapiTTSProvider          (services/sapi_tts_provider.py — SAPI5/Windows, offline)

`create_tts_service()` decide qual usar e nunca levanta exceção. O SAPI5 é
nativo do Windows (nenhum modelo para baixar, nenhuma dependência de rede) —
ver `frontend/README.md`, seção Voice Foundation, para detalhes e limitações.
Este módulo não importa `pyttsx3` — só `services/sapi_tts_provider.py` faz
isso (mesmo padrão de `services/stt_service.py`/`vosk_stt_provider.py`).
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class TTSUnavailableError(Exception):
    """Levantada quando não é possível sintetizar/reproduzir fala."""


class TextToSpeechService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Engine de síntese de voz pronto para uso."""

    @abstractmethod
    async def speak(self, text: str) -> None:
        """Fala um texto. Não bloqueia o event loop (roda em executor).
        Textos vazios são um no-op silencioso."""

    @abstractmethod
    async def stop(self) -> None:
        """Interrompe a fala em andamento, se houver. No-op se não houver
        nada tocando."""


class UnavailableTTSService(TextToSpeechService):
    def is_available(self) -> bool:
        return False

    async def speak(self, text: str) -> None:
        raise TTSUnavailableError("Síntese de voz não está disponível.")

    async def stop(self) -> None:
        return None


def create_tts_service(settings: "Settings") -> TextToSpeechService:
    """Decide qual `TextToSpeechService` usar:

        dependência (pyttsx3) ou SAPI5 ausente/falha  -> UnavailableTTSService
        tudo presente                                  -> SapiTTSProvider

    Ao contrário do STT, o TTS não depende de nenhum modelo baixado — usa as
    vozes já instaladas no Windows.
    """
    try:
        from services.sapi_tts_provider import SapiTTSProvider

        return SapiTTSProvider(voice_name=settings.tts_voice)
    except Exception as exc:
        logger.info("TTS indisponível (%s); JARVIS segue funcionando normalmente sem voz de saída.", exc)
        return UnavailableTTSService()
