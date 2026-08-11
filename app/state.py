"""Estado atual de execução do JARVIS."""

from enum import Enum


class JarvisState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING_SPEECH = "processing_speech"
    THINKING = "thinking"
    WORKING = "working"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SPEAKING = "speaking"
    ERROR = "error"
