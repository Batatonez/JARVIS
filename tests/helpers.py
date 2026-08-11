"""Utilitários compartilhados pelos testes — nunca tocam a memória real, um
microfone real ou um engine de TTS real."""

from pathlib import Path

from app.application import JarvisApplication
from app.core import JarvisCore
from services.ai_service import AIService, UnavailableAIService
from services.memory_service import MemoryService
from services.stt_service import UnavailableSTTService
from services.tts_service import UnavailableTTSService
from services.voice_service import VoiceService


def build_isolated_core(tmp_path: Path, *, ai_service: AIService | None = None) -> JarvisCore:
    """Cria um JarvisCore com MemoryService apontando para arquivos temporários.

    Por padrão usa `UnavailableAIService`, para que os testes nunca dependam
    de `ANTHROPIC_API_KEY` estar (ou não) configurada no ambiente de quem os
    executa, e nunca cheguem perto de fazer uma chamada real à API.
    """
    profile_path = tmp_path / "profile.md"
    preferences_path = tmp_path / "preferences.md"
    profile_path.write_text("# Perfil de teste", encoding="utf-8")
    preferences_path.write_text("# Preferências de teste", encoding="utf-8")

    memory_service = MemoryService(profile_path, preferences_path)
    return JarvisCore(
        memory_service=memory_service,
        ai_service=ai_service or UnavailableAIService(),
    )


def build_isolated_voice_service(core: JarvisCore) -> VoiceService:
    """VoiceService com STT/TTS indisponíveis por padrão — mesmo raciocínio
    de `UnavailableAIService` em `build_isolated_core`: os testes nunca devem
    depender de (nem acidentalmente tocar) um microfone ou engine de TTS
    real, esteja ele instalado ou não na máquina que roda a suíte."""
    return VoiceService(core.settings, core.event_bus, stt=UnavailableSTTService(), tts=UnavailableTTSService())


def build_isolated_application(
    tmp_path: Path, *, ai_service: AIService | None = None, voice_service: VoiceService | None = None
) -> JarvisApplication:
    """Cria uma JarvisApplication sobre um JarvisCore isolado (ver `build_isolated_core`)."""
    core = build_isolated_core(tmp_path, ai_service=ai_service)
    return JarvisApplication(core, voice_service=voice_service or build_isolated_voice_service(core))
