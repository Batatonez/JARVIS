# app/

Aplicativo/núcleo principal do JARVIS.

## Status: implementado (JARVIS Backend v0.4)

**Application Layer — a fronteira estável para qualquer frontend (ver [`docs/application-api.md`](../docs/application-api.md)):**

- **`application.py`** — `JarvisApplication`: única porta de entrada que um frontend deve usar. Lifecycle (`start`/`stop`), `send_message`/`cancel_current_request`/`new_conversation`, `get_status`/`get_messages`/`memory_status`, stream de eventos (`subscribe`/`unsubscribe`/`events`).
- **`models.py`** — modelos de domínio (`Message`, `AssistantResponse`, `StatusSnapshot`, `AppEvent`, `PermissionRequest`, etc.) — só biblioteca padrão, nunca um tipo do Claude Agent SDK.
- **`conversation.py`** — `Conversation`: histórico de mensagens da sessão atual, só em RAM (limite configurável). Diferente de `memory/` — nunca escrito em disco.
- **`status.py`** — `build_status_snapshot(core)`: fonte única do status, usada tanto por `/status` quanto por `JarvisApplication.get_status()`.
- **`permissions.py`** — `PermissionService`: fundação em memória para permissões futuras (READ/ACTION/DANGEROUS), não conectada a nenhuma ferramenta real ainda.

**Núcleo (por baixo da Application Layer):**

- **`terminal.py`** — apresentação: loop `async` de input/print no terminal (Ctrl+C, EOF e `/exit` tratados sem traceback), falando com `JarvisApplication` — não conhece `JarvisCore` nem `Orchestrator`.
- **`core.py`** — `JarvisCore`: fachada que conecta configuração, serviços (`services/`) e orquestração; guarda o estado atual e o ciclo de vida assíncrono (`start`/`stop`/`restart_ai_session`), incluindo montar o contexto de memória entregue à IA e cair para `UnavailableAIService` se a conexão falhar.
- **`orchestrator.py`** — `Orchestrator`: decide se uma entrada é um comando interno ou uma mensagem comum, e roteia para `commands.py` ou para o `AIService` (`await ask(...)`), devolvendo o texto cru da resposta. Não conhece Claude Agent SDK nem `JarvisApplication`.
- **`commands.py`** — `CommandRegistry` com os comandos internos, síncronos: `/help`, `/status`, `/memory`, `/clear`, `/exit` (alias `/quit`). (`/new`/`/reset` é tratado um nível acima, em `JarvisApplication`, porque depende do histórico de conversa.)
- **`state.py`** — `JarvisState`: enum simples do estado atual (`IDLE`, `THINKING`, `ERROR`, etc.).

## Responsabilidade futura

Quando uma interface gráfica (HUD) for criada, ela deve falar com `JarvisApplication` sem duplicar lógica — apenas somar ou substituir `terminal.py` por uma camada de apresentação gráfica. Entrada de voz e captura de tela/HUD visual ainda não existem.

## Fora de escopo aqui

Nenhuma tecnologia de interface gráfica foi escolhida ainda (Electron, Tauri, PySide, web etc.) — isso será decidido quando a etapa de interface gráfica começar, não antes.
