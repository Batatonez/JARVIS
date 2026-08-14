"""Enumeração e escolha de dispositivos de entrada de áudio (v1.3).

Até a v1.2 o JARVIS abria sempre o microfone PADRÃO do sistema e nunca
perguntava nada — quem tem duas placas de som, um headset USB e uma webcam
com microfone ficava refém do que o Windows tivesse elegido. Este módulo
existe para o HUD poder listar, escolher, testar e lembrar um microfone.

**Identificador estável (item 14 da v1.3):** o índice numérico do PortAudio
NÃO é estável — ele muda quando um dispositivo USB é conectado/removido ou
depois de um reboot, e persistir "device 3" faria o JARVIS abrir o
dispositivo errado silenciosamente. Por isso persistimos `AudioDevice.key`
(host API + nome), que é o que um humano reconheceria, e só usamos o índice
dentro da sessão em que ele foi consultado.

Este módulo importa `sounddevice` de forma preguiçosa (dentro das funções),
pelo mesmo motivo de `services/stt_service.py` não importar `vosk`: um
ambiente sem PortAudio precisa continuar importando o JARVIS normalmente e
simplesmente ver "nenhum microfone".
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Chave reservada para "usar o que o sistema considera padrão". Não é o nome
# de nenhum dispositivo real, então nunca colide com um `AudioDevice.key`.
SYSTEM_DEFAULT_KEY = ""

_FALLBACK_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class AudioDevice:
    """Um dispositivo de ENTRADA. `index` só vale para a enumeração que o
    produziu; `key` é o que pode ser guardado no banco."""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_samplerate: int
    is_system_default: bool = False

    @property
    def key(self) -> str:
        """Identificador estável entre execuções: host API + nome. Dois
        dispositivos com o mesmo nome em host APIs diferentes (MME vs. WASAPI,
        comum no Windows) são entradas distintas e não podem colidir."""
        return f"{self.host_api}:{self.name}"

    @property
    def label(self) -> str:
        """Texto para o HUD — o nome cru já é o que o usuário reconhece; a
        host API só aparece para desempatar nomes repetidos (ver
        `list_input_devices`)."""
        return self.name


def _host_api_names(sd) -> dict[int, str]:
    try:
        return {index: api["name"] for index, api in enumerate(sd.query_hostapis())}
    except Exception:
        return {}


def list_input_devices() -> list[AudioDevice]:
    """TODOS os dispositivos de entrada disponíveis (item 13 da v1.3) — nunca
    apenas `device 0`. Lista vazia quando não há PortAudio/microfone; nunca
    levanta exceção."""
    try:
        import sounddevice as sd
    except Exception as exc:
        logger.info("Enumeração de áudio indisponível (%s).", exc)
        return []

    try:
        raw_devices = sd.query_devices()
        default_input = sd.default.device[0]
    except Exception as exc:
        logger.info("Não foi possível consultar dispositivos de áudio (%s).", exc)
        return []

    api_names = _host_api_names(sd)
    devices: list[AudioDevice] = []
    for index, raw in enumerate(raw_devices):
        channels = int(raw.get("max_input_channels", 0) or 0)
        if channels < 1:
            continue  # dispositivo só de saída
        rate = raw.get("default_samplerate") or _FALLBACK_SAMPLE_RATE
        devices.append(
            AudioDevice(
                index=index,
                name=str(raw.get("name", f"Dispositivo {index}")).strip(),
                host_api=api_names.get(int(raw.get("hostapi", -1)), "?"),
                max_input_channels=channels,
                default_samplerate=int(round(float(rate))),
                is_system_default=(index == default_input),
            )
        )
    return devices


def default_input_device() -> AudioDevice | None:
    """O dispositivo que o sistema considera padrão. Se o PortAudio não
    marcar nenhum, cai no primeiro da lista — melhor um microfone real do que
    nenhum."""
    devices = list_input_devices()
    if not devices:
        return None
    for device in devices:
        if device.is_system_default:
            return device
    return devices[0]


@dataclass(frozen=True)
class DeviceResolution:
    """Resultado de `resolve_input_device`. `fell_back` é o que permite ao HUD
    avisar discretamente que o microfone salvo sumiu (item 14) em vez de
    trocar de dispositivo em silêncio — ou pior, quebrar."""

    device: AudioDevice | None
    fell_back: bool = False
    requested_key: str = SYSTEM_DEFAULT_KEY


def resolve_input_device(preferred_key: str | None) -> DeviceResolution:
    """Regra do item 13/14:

        preferência salva existe na máquina  -> usa ela
        preferência salva sumiu              -> default do sistema + `fell_back`
        sem preferência                      -> default do sistema
        nenhum dispositivo                   -> `device=None` (nunca exceção)
    """
    devices = list_input_devices()
    if not devices:
        return DeviceResolution(device=None, requested_key=preferred_key or SYSTEM_DEFAULT_KEY)

    if not preferred_key:
        return DeviceResolution(device=default_input_device())

    for device in devices:
        if device.key == preferred_key:
            return DeviceResolution(device=device, requested_key=preferred_key)

    logger.info("Microfone salvo não está mais disponível (%r); usando o padrão do sistema.", preferred_key)
    return DeviceResolution(device=default_input_device(), fell_back=True, requested_key=preferred_key)
