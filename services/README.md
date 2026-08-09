# services/

Serviços internos usados pelo `Orchestrator` (`app/orchestrator.py`).

## Status: implementado parcialmente (JARVIS Core v0.1)

- **`memory_service.py`** — **implementado.** `MemoryService` lê `memory/profile.md` e `memory/preferences.md` de forma somente-leitura, com tratamento de erro para arquivo ausente ou ilegível (`MemoryUnavailableError`). Escrita de memória é planejada, com regras próprias.
- **`event_bus.py`** — **implementado.** `EventBus`: pub/sub síncrono e em memória, sem dependências externas. Permite que o Core emita eventos (`jarvis.started`, `state.changed`, `message.received`, etc.) sem conhecer quem vai consumi-los — importante para o futuro HUD.
- **`ai_service.py`** — **abstração implementada, provider real planejado.** Define `AIService` (interface) e `UnavailableAIService` (placeholder desta versão). Nenhuma chamada de IA acontece ainda.

## Planejado

- Provider real de IA (ex.: `ClaudeProvider`, conectando ao Claude).
- Serviço de voz (STT/TTS).

Gerenciamento de estado da sessão atual não vive aqui — fica em `app/state.py` e `app/core.py`, por ser intrínseco ao ciclo de vida do `JarvisCore` e não um serviço externo substituível.
