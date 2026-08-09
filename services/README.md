# services/

Serviços internos usados pelo `Orchestrator` (`app/orchestrator.py`).

## Status: implementado (JARVIS Core v0.3)

- **`memory_service.py`** — **implementado.** `MemoryService` lê `memory/profile.md` e `memory/preferences.md` de forma somente-leitura, com tratamento de erro para arquivo ausente ou ilegível (`MemoryUnavailableError`). Escrita de memória é planejada, com regras próprias.
- **`event_bus.py`** — **implementado.** `EventBus`: pub/sub síncrono e em memória, sem dependências externas. Eventos hoje incluem `jarvis.started/stopped`, `state.changed`, `message.received/responded`, `command.executed`, `ai.connecting/connected/disconnected`, `ai.request.started/completed/failed`.
- **`ai_service.py`** — **implementado.** Define `AIService` (interface com lifecycle assíncrono `start`/`ask`/`close`, mais `session_active` e `backend_name`), `UnavailableAIService` (placeholder) e `create_ai_service(settings)` — a factory que decide qual usar. Não importa o Claude Agent SDK.
- **`claude_agent_provider.py`** — **implementado.** `ClaudeAgentProvider`: provider real de IA usando o **Claude Agent SDK** (pacote `claude-agent-sdk`, módulo `claude_agent_sdk`, via `ClaudeSDKClient`). Único módulo do projeto que conhece o SDK; erros oficiais (`CLINotFoundError`, `CLIConnectionError`, `ProcessError`, `CLIJSONDecodeError`, `ClaudeSDKError`, e qualquer exceção inesperada) nunca escapam crus — viram `ClaudeAgentProviderError` (subclasse de `AIServiceUnavailableError`) com mensagem segura, sem segredos. Ferramentas do agente ficam desabilitadas (`allowed_tools=[]` + callback `can_use_tool` que nega tudo) e `setting_sources=[]` evita herdar configuração pessoal do Claude Code da máquina.
- **`runtime_identity.py`** — **implementado.** Instruções de persona do JARVIS em runtime (nome, idioma, tom, regras de memória/segurança) — não confundir com `CLAUDE.md`, que orienta o Claude Code *desenvolvendo* o projeto.

## Preparado, mas não ativado

`ClaudeAgentProvider` está implementado e testado com fakes/mocks (`tests/test_claude_agent_provider.py`), mas nenhuma chamada real ao Agent SDK foi feita — não há `ANTHROPIC_API_KEY` configurada neste ambiente. Configurar a variável (ver [`.env.example`](../.env.example)) ativa o provider sem qualquer mudança de código.

## Planejado

- Serviço de voz (STT/TTS).
- Outros providers de IA além do Claude, se necessário (bastaria implementar `AIService` e ajustar `create_ai_service`).
- Ruflo como camada opcional de orquestração multiagente (ver [`docs/architecture.md`](../docs/architecture.md)) — não é um `AIService`, seria acionado separadamente pelo Orchestrator.

Gerenciamento de estado da sessão atual não vive aqui — fica em `app/state.py` e `app/core.py`, por ser intrínseco ao ciclo de vida do `JarvisCore` e não um serviço externo substituível.
