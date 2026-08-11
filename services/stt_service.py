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

**v0.9 — `status` (diagnóstico real, não "unavailable" genérico):** o botão
de microfone do HUD precisa saber SE PRECISA de setup (modelo não
instalado) versus se realmente não há nada a fazer (sem microfone, ou
dependência ausente) — ver `STTStatus`. `create_stt_service()` sempre
consegue distinguir "modelo ausente" das outras causas porque consulta
`VoiceModelManager.is_installed` antes de tentar construir o provider real.

Push-to-talk: `start_listening()` abre o microfone e começa a capturar;
`stop_and_transcribe()` para a captura e devolve o texto reconhecido.
Nenhum áudio é gravado em disco por padrão — ver `vosk_stt_provider.py`.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class STTStatus(Enum):
    READY = "ready"  # modelo instalado + microfone presente — pronto para gravar
    SETUP_REQUIRED = "setup_required"  # dependências ok, mas o modelo Vosk não está instalado
    NO_MICROPHONE = "no_microphone"  # modelo instalado, mas nenhum microfone foi detectado
    UNAVAILABLE = "unavailable"  # dependência ausente, kill-switch desligado, ou outra falha real


class STTUnavailableError(Exception):
    """Levantada quando não é possível capturar ou transcrever áudio."""


class SpeechToTextService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Engine/modelo prontos para uso (independente de haver microfone)."""

    @property
    @abstractmethod
    def status(self) -> STTStatus:
        """Diagnóstico real do porquê — usado pelo HUD para decidir o que
        mostrar/fazer (nunca um "unavailable" genérico quando dá pra ser
        específico)."""

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
    """Placeholder: nenhum engine de reconhecimento de fala está pronto.
    `status` carrega o motivo real (setup pendente vs. indisponível de
    verdade) — nunca escondido atrás de um booleano só."""

    def __init__(self, status: STTStatus = STTStatus.UNAVAILABLE) -> None:
        self._status = status

    def is_available(self) -> bool:
        return False

    @property
    def status(self) -> STTStatus:
        return self._status

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

        voice_input_enabled=False              -> UnavailableSTTService(UNAVAILABLE)
        modelo Vosk ausente em stt_model_path   -> UnavailableSTTService(SETUP_REQUIRED)
        dependência (vosk/sounddevice) ausente  -> UnavailableSTTService(UNAVAILABLE)
        tudo presente                           -> VoskSTTProvider (READY ou NO_MICROPHONE)

    Nunca baixa nenhum modelo. Ver `services/vosk_model_manager.py` para o
    fluxo de instalação (sob consentimento explícito do usuário, pelo HUD).
    """
    if not settings.voice_input_enabled:
        return UnavailableSTTService(STTStatus.UNAVAILABLE)

    if not settings.stt_model_path.is_dir():
        logger.info("STT: modelo Vosk não instalado em %s (setup necessário).", settings.stt_model_path)
        return UnavailableSTTService(STTStatus.SETUP_REQUIRED)

    try:
        from services.vosk_stt_provider import VoskSTTProvider

        return VoskSTTProvider(model_path=settings.stt_model_path)
    except Exception as exc:
        logger.info("STT indisponível (%s); JARVIS segue funcionando normalmente por texto.", exc)
        return UnavailableSTTService(STTStatus.UNAVAILABLE)
