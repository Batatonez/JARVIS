# app/

Aplicativo/núcleo principal do JARVIS.

## Status: implementado parcialmente (JARVIS Core v0.3)

- **`terminal.py`** — apresentação: loop `async` de input/print no terminal (Ctrl+C, EOF e `/exit` tratados sem traceback), rodando dentro do único event loop criado por `main.py`. É a única camada de apresentação hoje.
- **`core.py`** — `JarvisCore`: fachada que conecta configuração, serviços (`services/`) e orquestração; guarda o estado atual e o ciclo de vida assíncrono (`start`/`stop`), incluindo montar o contexto de memória entregue à IA e cair para `UnavailableAIService` se a conexão falhar.
- **`orchestrator.py`** — `Orchestrator`: decide se uma entrada é um comando interno ou uma mensagem comum, e roteia para `commands.py` ou para o `AIService` (`await ask(...)`). Não conhece Claude Agent SDK.
- **`commands.py`** — `CommandRegistry` com os comandos internos, síncronos: `/help`, `/status`, `/memory`, `/clear`, `/exit` (alias `/quit`).
- **`state.py`** — `JarvisState`: enum simples do estado atual (`IDLE`, `THINKING`, `ERROR`, etc.).

## Responsabilidade futura

Quando uma interface gráfica (HUD) for criada, ela deve reutilizar `JarvisCore` e `Orchestrator` sem duplicar lógica — apenas somar ou substituir `terminal.py` por uma camada de apresentação gráfica. Entrada de voz e captura de tela/HUD visual ainda não existem.

## Fora de escopo aqui

Nenhuma tecnologia de interface gráfica foi escolhida ainda (Electron, Tauri, PySide, web etc.) — isso será decidido quando a etapa de interface gráfica começar, não antes.
