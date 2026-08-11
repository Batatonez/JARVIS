"""Utilitários compartilhados pelos testes — nunca tocam a memória real, um
microfone real ou um engine de TTS real."""

from collections.abc import Callable
from pathlib import Path

from app.account_manager import AccountManager
from app.application import JarvisApplication
from app.core import JarvisCore
from config.settings import Settings
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


def build_isolated_settings(tmp_path: Path) -> Settings:
    """`Settings` com todo caminho de dado (DB, contas, memória por usuário,
    token de sessão, modelos de voz) apontando para dentro de `tmp_path` —
    nenhum teste de contas/chats/voz toca `data/` nem `memory/` reais do
    projeto (ver app/account_manager.py)."""
    return Settings(
        project_root=tmp_path,
        memory_dir=tmp_path / "memory",
        profile_path=tmp_path / "memory" / "profile.md",
        preferences_path=tmp_path / "memory" / "preferences.md",
        log_dir=tmp_path / "logs",
        log_path=tmp_path / "logs" / "jarvis.log",
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "jarvis.db",
        users_dir=tmp_path / "data" / "users",
        session_token_path=tmp_path / "data" / "session.local",
        stt_models_dir=tmp_path / "data" / "models" / "vosk",
        stt_model_path=tmp_path / "data" / "models" / "vosk" / "vosk-model-small-pt",
    )


def build_isolated_account_manager(
    tmp_path: Path,
    *,
    ai_service_factory: Callable[[], AIService] | None = None,
    voice_service_factory: Callable[[JarvisCore], VoiceService] | None = None,
) -> AccountManager:
    """`AccountManager` isolado (SQLite + memória por conta dentro de
    `tmp_path`). Por padrão, `JarvisCore`/`JarvisApplication` construídos por
    sessão usam os mesmos serviços seguros de fallback da produção
    (`UnavailableAIService`, STT/TTS reais porém sem modelo/hardware
    presumidos) — passe as factories só quando o teste precisar de um fake
    controlável (ex.: `FakeAIService` para simular uma resposta)."""
    settings = build_isolated_settings(tmp_path)
    return AccountManager(
        settings,
        ai_service_factory=ai_service_factory,
        voice_service_factory=voice_service_factory,
    )


def build_isolated_bridge(
    tmp_path: Path,
    *,
    dev_mode: bool = True,
    ai_service_factory: Callable[[], AIService] | None = None,
    voice_service_factory: Callable[[JarvisCore], VoiceService] | None = None,
):
    """`JarvisBridge` isolado (v0.9: auth-first, ver frontend/bridge.py).
    Importa Qt só dentro da função — módulos de teste que não mexem com o
    Bridge (ex.: test_application.py) não pagam o custo de importar PySide6."""
    from frontend.bridge import JarvisBridge
    from services.vosk_model_manager import VoiceModelManager

    settings = build_isolated_settings(tmp_path)
    account_manager = AccountManager(
        settings,
        ai_service_factory=ai_service_factory,
        voice_service_factory=voice_service_factory,
    )
    voice_model_manager = VoiceModelManager(models_dir=settings.stt_models_dir)
    return JarvisBridge(account_manager, voice_model_manager, dev_mode=dev_mode)
