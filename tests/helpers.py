"""Utilitários compartilhados pelos testes — nunca tocam a memória real."""

from pathlib import Path

from app.core import JarvisCore
from services.ai_service import AIService, UnavailableAIService
from services.memory_service import MemoryService


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
