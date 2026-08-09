# services/

Serviços internos usados pelo `Orchestrator` (`app/orchestrator.py`).

## Status: implementado parcialmente (JARVIS Core v0.2)

- **`memory_service.py`** — **implementado.** `MemoryService` lê `memory/profile.md` e `memory/preferences.md` de forma somente-leitura, com tratamento de erro para arquivo ausente ou ilegível (`MemoryUnavailableError`). Escrita de memória é planejada, com regras próprias.
- **`event_bus.py`** — **implementado.** `EventBus`: pub/sub síncrono e em memória, sem dependências externas. Permite que o Core emita eventos (`jarvis.started`, `state.changed`, `message.received`, etc.) sem conhecer quem vai consumi-los — importante para o futuro HUD.
- **`ai_service.py`** — **implementado.** Define `AIService` (interface), `UnavailableAIService` (placeholder) e `create_ai_service(settings)` — a factory que decide qual usar. Não importa a SDK da Anthropic.
- **`claude_provider.py`** — **implementado.** `ClaudeProvider`: provider real de IA usando o SDK oficial `anthropic`. Único módulo do projeto que conhece a API da Anthropic; erros da API nunca escapam como exceção não tratada — viram `ClaudeProviderError` (subclasse de `AIServiceUnavailableError`) com mensagem segura, sem segredos.

## Planejado

- Serviço de voz (STT/TTS).
- Outros providers de IA além do Claude, se necessário (bastaria implementar `AIService` e ajustar `create_ai_service`).

Gerenciamento de estado da sessão atual não vive aqui — fica em `app/state.py` e `app/core.py`, por ser intrínseco ao ciclo de vida do `JarvisCore` e não um serviço externo substituível.
