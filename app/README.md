# app/

Aplicativo/núcleo principal do JARVIS.

## Status: implementado parcialmente (JARVIS Core v0.1)

- **`terminal.py`** — apresentação: loop de input/print no terminal (Ctrl+C, EOF e `/exit` tratados sem traceback). É a única camada de apresentação hoje.
- **`core.py`** — `JarvisCore`: fachada que conecta configuração, serviços (`services/`) e orquestração; guarda o estado atual e o ciclo de vida (`start`/`stop`).
- **`orchestrator.py`** — `Orchestrator`: decide se uma entrada é um comando interno ou uma mensagem comum, e roteia para `commands.py` ou para o serviço de IA (ainda indisponível).
- **`commands.py`** — `CommandRegistry` com os comandos internos: `/help`, `/status`, `/memory`, `/clear`, `/exit` (alias `/quit`).
- **`state.py`** — `JarvisState`: enum simples do estado atual (`IDLE`, `THINKING`, `WORKING`, etc.).

## Responsabilidade futura

Quando uma interface gráfica (HUD) for criada, ela deve reutilizar `JarvisCore` e `Orchestrator` sem duplicar lógica — apenas somar ou substituir `terminal.py` por uma camada de apresentação gráfica. Entrada de voz e captura de tela/HUD visual ainda não existem.

## Fora de escopo aqui

Nenhuma tecnologia de interface gráfica foi escolhida ainda (Electron, Tauri, PySide, web etc.) — isso será decidido quando a etapa de interface gráfica começar, não antes.
