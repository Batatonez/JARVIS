"""Abstração de Speech-to-Text (STT) do JARVIS.

    SpeechToTextService
        |
        UnavailableSTTService   (sem microfone/modelo/dependência instalada)
        VoskSTTProvider          (services/vosk_stt_provider.py — Vosk, offline)

`create_stt_service()` decide qual usar e nunca levanta exceção nem baixa
nada — se o modelo Vosk não estiver no caminho configurado
(`settings.stt_model_path`), o JARVIS segue funcionando normalmente por
texto (voz é uma capability opcional). Este módulo não importa `vosk` nem
`sounddevice` — só `services/vosk_stt_provider.py` faz isso, e só quando
`create_stt_service()` realmente precisa construir um provider real (mesmo
padrão de `services/ai_service.py` + `services/claude_agent_provider.py`).

Push-to-talk: `start_listening()` abre o microfone e começa a capturar;
`stop_and_transcribe()` para a captura e devolve o texto reconhecido.
Nenhum áudio é gravado em disco por padrão — ver `vosk_stt_provider.py`.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class STTUnavailableError(Exception):
    """Levantada quando não é possível capturar ou transcrever áudio."""


class SpeechToTextService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Engine/modelo prontos para uso (independente de haver microfone)."""

    @property
    @abstractmethod
    def microphone_available(self) -> bool:
        """Existe um dispositivo de entrada de áudio padrão no sistema."""

    @abstractmethod
    async def start_listening(self, *, on_level: Callable[[float], None] | None = None) -> None:
        """Abre o microfone e começa a capturar. `on_level` (opcional) é
        chamado com um valor 0.0-1.0 de nível de áudio, já throttled pelo
        provider (nunca em alta frequência)."""

    @abstractmethod
    async def stop_and_transcribe(self) -> str:
        """Para a captura e devolve o texto reconhecido (pode ser string
        vazia se nada foi entendido). Levanta `STTUnavailableError` em falha
        real (dispositivo caiu, engine travou etc.)."""

    @abstractmethod
    async def cancel(self) -> None:
        """Para a captura sem transcrever, descartando o áudio capturado."""


class UnavailableSTTService(SpeechToTextService):
    """Placeholder: nenhum engine de reconhecimento de fala está pronto
    (kill-switch desligado, dependência ausente, ou modelo não encontrado)."""

    def is_available(self) -> bool:
        return False

    @property
    def microphone_available(self) -> bool:
        return False

    async def start_listening(self, *, on_level: Callable[[float], None] | None = None) -> None:
        raise STTUnavailableError("Reconhecimento de fala não está disponível.")

    async def stop_and_transcribe(self) -> str:
        raise STTUnavailableError("Reconhecimento de fala não está disponível.")

    async def cancel(self) -> None:
        return None


def create_stt_service(settings: "Settings") -> SpeechToTextService:
    """Decide qual `SpeechToTextService` usar:

        voice_input_enabled=False              -> UnavailableSTTService
        dependência (vosk/sounddevice) ausente  -> UnavailableSTTService
        modelo ausente em settings.stt_model_path -> UnavailableSTTService
        tudo presente                           -> VoskSTTProvider

    Nunca baixa nenhum modelo. Ver `frontend/README.md` para o comando de
    download manual do modelo Vosk pt-BR.
    """
    if not settings.voice_input_enabled:
        return UnavailableSTTService()

    try:
        from services.vosk_stt_provider import VoskSTTProvider

        return VoskSTTProvider(model_path=settings.stt_model_path)
    except Exception as exc:
        logger.info(
            "STT indisponível (%s); JARVIS segue funcionando normalmente por texto.", exc
        )
        return UnavailableSTTService()
