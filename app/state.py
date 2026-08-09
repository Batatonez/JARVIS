"""Estado atual de execução do JARVIS."""

from enum import Enum


class JarvisState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    WORKING = "working"
    WAITING_CONFIRMATION = "waiting_confirmation"
    SPEAKING = "speaking"
    ERROR = "error"
