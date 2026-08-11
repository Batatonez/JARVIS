# Arquitetura do JARVIS

> **Aviso:** este documento mistura o que já existe (**IMPLEMENTADO**) com o que está **PREPARADO, MAS NÃO ATIVADO** e o que é puro **PLANEJADO**. Cada seção diz explicitamente qual é o caso. Não presuma que algo existe só porque está no diagrama — o diagrama descreve a arquitetura-alvo completa, não o estado atual.

## Estado atual em uma frase

**JARVIS Frontend/HUD v0.5**: a primeira interface gráfica real (PySide6/QML), consumindo a mesma Application Layer (`JarvisApplication`) que o terminal — histórico de conversa em runtime, stream de eventos, status consolidado, cancelamento, tudo sobre o `JarvisCore`/`Orchestrator`/`ClaudeAgentProvider` já existentes, sem nenhuma API key configurada neste ambiente de desenvolvimento. O comportamento padrão observável hoje continua sendo o fallback seguro (`UnavailableAIService`) — o HUD mostra isso claramente (`AI OFFLINE`), sem fingir conexão.

## Visão geral (arquitetura-alvo, planejada)

```
                    ┌─────────────────────┐
                    │      FRONTENDS      │
                    │ HUD (v0.5) / Voice  │
                    │   / CLI (terminal)  │
                    └──────────┬──────────┘
                               │
                               ↓
                    ┌─────────────────────┐
                    │ JARVIS APPLICATION  │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    ↓                     ↓
                 Core                Event Stream
                    │
               Orchestrator
                    │
        ┌───────────┼────────────┐
        ↓           ↓            ↓
    AIService     Memory      Services
        ↓
ClaudeAgentProvider


FUTURO (planejado, não implementado):

Orchestrator
    ↓
AgentOrchestration
    ↓
Ruflo
```

Em paralelo:

```
Claude Code  ↔  Memory
Hooks        →  App / HUD
Voice STT    →  Application
Application  →  TTS
```

## Application Layer — a fronteira entre Core e frontend

**Status: implementado (v0.4).** `JarvisApplication` (`app/application.py`) é a **única** porta de entrada que um frontend deve usar — terminal e HUD hoje, voz no futuro. Documentação completa da API pública em [`docs/application-api.md`](application-api.md); aqui vai só o desenho:

```
Frontend (terminal, futuro HUD, futura voz)
    ↓
JarvisApplication
    ├── send_message() / cancel_current_request() / new_conversation()
    ├── get_status() / get_messages() / memory_status()
    ├── subscribe() / unsubscribe() / events()
    └── permissions  (PermissionService — fundação, não conectada a ferramentas)
    ↓
JarvisCore  →  Orchestrator  →  AIService  →  ClaudeAgentProvider
```

Um frontend **nunca** deve fazer `from services.claude_agent_provider import ClaudeAgentProvider`, `memory_service.get_profile()` ou `orchestrator.handle(...)` diretamente — só `JarvisApplication`. Modelos de domínio (`app/models.py`: `Message`, `AssistantResponse`, `StatusSnapshot`, `AppEvent`, `PermissionRequest`, etc.) usam só a biblioteca padrão e nunca carregam um tipo do Claude Agent SDK — testado explicitamente (`tests/test_application.py::NoSdkLeakageTests`).

**Conversation vs. Memory** — dois conceitos deliberadamente separados:
- `app/conversation.py` (`Conversation`) — histórico da sessão de chat atual, só em RAM, com limite configurável (`JARVIS_MAX_CONVERSATION_MESSAGES`, padrão 200). Desaparece ao encerrar o JARVIS. Nunca é escrito em `memory/` ou `daily/`.
- `memory/` — memória persistente sobre o usuário (perfil, preferências). Somente leitura em runtime, inalterada por conversas.

**Concorrência** — política escolhida: **uma requisição ativa por conversa**, rejeição limpa (não fila). `send_message()` verifica `self._current_request_task`; se ocupado, devolve `AssistantResponse(status=ERROR, error.code=JARVIS_BUSY)` na hora, sem enfileirar. Mais simples e previsível para uma GUI (estado "ocupado" explícito) do que uma fila implícita — ver `tests/test_application.py::BusyAndConcurrencyTests`.

**Cancelamento** — `cancel_current_request()` usa `asyncio.Task.cancel()` (nunca mata processo). O `finally` já existente em `Orchestrator._handle_message` garante que o estado volta para `IDLE` mesmo quando a tarefa é cancelada no meio do caminho — testado em `tests/test_application.py::CancellationTests`.

**Erros para a interface** — `send_message()` nunca propaga uma exceção crua para quem chama: falhas esperadas (`AI_UNAVAILABLE`, `JARVIS_BUSY`) e inesperadas (`INTERNAL_ERROR`) sempre viram `AssistantResponse` com `status`/`error.code` estruturados. O frontend distingue sucesso/cancelado/erro sem nunca precisar analisar texto humano.

**Eventos** — `JarvisApplication` relaia uma parte dos eventos do `EventBus` interno (`jarvis.started/stopped`, `state.changed`, `ai.connected/disconnected`) e emite diretamente os seus próprios, mais ricos que os internos (`message.received`, `response.started/completed/failed`, `conversation.started/cleared`, `jarvis.stopping`, `permission.requested/resolved`) — nenhum evento é emitido duas vezes pelo mesmo motivo em camadas diferentes.

**Streaming (preparado, não exposto)** — `send_message()` devolve o texto final via `AssistantResponse.content` (`str`), mas o contrato de eventos já inclui `response.started` → `response.completed`/`response.failed`; adicionar `response.delta` para token-a-token é uma extensão local a `send_message()`/`ClaudeAgentProvider.ask()`, sem mudar a API pública.

## Como isso mapeia para o código hoje (v0.5)

Existem dois frontends reais, os dois falando com o mesmo `JarvisApplication` — nenhum fala com `JarvisCore` diretamente. Todo o caminho é assíncrono (`asyncio`), cada frontend com seu próprio event loop (`main.py` usa `asyncio.run(...)`; `frontend/launcher.py` usa `PySide6.QtAsyncio.run(...)`, ver seção seguinte):

```
main.py  →  asyncio.run(...)              frontend/__main__.py  →  QtAsyncio.run(...)
  ↓                                          ↓
app/terminal.py (async)                    frontend/launcher.py + frontend/bridge.py (JarvisBridge)
  ↓                                          ↓
                    app/application.py     (JarvisApplication: fronteira estável — async)
                              ↓
                    app/core.py             (JarvisCore: fachada, estado, ciclo de vida — async)
                              ↓
                    app/orchestrator.py     (Orchestrator: comando vs. mensagem — async)
                              ↓
                    app/commands.py         (comandos internos, síncronos: /help /status /memory /new /clear /exit)
                              ↓
                    services/                (memory_service, ai_service + create_ai_service, claude_agent_provider, runtime_identity, event_bus)
```

## Frontend/HUD — [`frontend/`](../frontend/)

**Status: implementado (v0.5).** Segundo frontend, ao lado do terminal — não substitui `app/terminal.py`, os dois continuam existindo e ambos falam só com `JarvisApplication`. Documentação completa em [`frontend/README.md`](../frontend/README.md); aqui vai só o desenho arquitetural:

```
JARVIS HUD (PySide6 / QML)
    ↓
frontend/bridge.py (JarvisBridge — QObject, sem lógica de domínio, só tradução)
    ↓
JarvisApplication
```

O QML **nunca** importa `app/core.py`, `app/application.py`, `services/` ou o Claude Agent SDK — só conhece `bridge`, exposto como propriedade de contexto (`engine.rootContext().setContextProperty("bridge", bridge)`). `JarvisBridge` expõe Properties Qt (`jarvisState`, `running`, `busy`, `memoryAvailable`, `aiConfigured`, `aiBackend`, `aiSessionActive`, `activeConversation`, `pendingPermission`, `messages`, `devMode`, `canClose`) e Slots (`sendMessage`, `cancelCurrentRequest`, `newConversation`, `approvePermission`, `denyPermission`, `requestShutdown`) — QML nunca interpreta string de `/status`.

**Orientado a eventos, nunca a polling**: na inicialização, `JarvisBridge.start()` chama `JarvisApplication.subscribe()` (síncrono — a fila é registrada imediatamente) e consome essa fila em uma única task de fundo; cada evento relevante dispara uma releitura pontual de `get_status()`/`get_messages()`, nunca em loop/timer.

**Integração Qt + asyncio**: `PySide6.QtAsyncio` (módulo oficial do Qt for Python, incluso no `PySide6` já instalado — nenhuma dependência extra como `qasync`) funde o event loop do Qt e do asyncio em um só processo, sem thread extra e sem recriar o loop por mensagem.

Quando o HUD é fechado, `Window.onClosing` intercepta e chama `bridge.requestShutdown()`, que cancela requisição pendente, encerra a sessão de IA (`JarvisApplication.stop()`) e só então deixa a janela fechar — sem tasks órfãs.

## Camada de IA: AIService → ClaudeAgentProvider

```
Orchestrator
    ↓
AIService                 (abstração — services/ai_service.py)
    ↓
    ├── UnavailableAIService     (fallback — sem API key)
    └── ClaudeAgentProvider      (services/claude_agent_provider.py — com API key)
            ↓
        ClaudeSDKClient          (claude_agent_sdk)
            ↓
        Claude Agent SDK
```

`create_ai_service(settings)` decide entre as duas implementações checando apenas a *presença* de `ANTHROPIC_API_KEY` no ambiente — quem efetivamente lê o valor da chave é o próprio Agent SDK ao conectar, não o JARVIS. O Orchestrator só conhece `AIService`: não importa `claude_agent_sdk`, não sabe o que é um `ClaudeSDKClient`, não trata autenticação.

**Lifecycle explícito** (`start` → `ask` → `ask` → ... → `close`), pensado para sessão contínua:

```
JarvisCore.start()
    ↓
AIService.start(memory_context=...)   # conecta uma vez, reutiliza a sessão
    ↓
AIService.ask(mensagem)   # 1ª mensagem
    ↓
AIService.ask(mensagem)   # 2ª mensagem — mesma sessão, mesmo client
    ↓
JarvisCore.stop()
    ↓
AIService.close()
```

`ClaudeAgentProvider.start()` é idempotente (chamar de novo não recria a sessão) e `close()` também (chamar em um provider já encerrado não faz nada). Se `start()` falhar (ex.: CLI do Agent SDK ausente, autenticação inválida), `JarvisCore` troca `self.ai_service` por um `UnavailableAIService` em runtime e segue normalmente — a falha nunca derruba o Core.

### Status: PREPARADO, MAS NÃO ATIVADO

A arquitetura acima está implementada e testada (com fakes/mocks — ver `tests/test_claude_agent_provider.py`), mas **nenhuma chamada real foi feita** neste ambiente: não há `ANTHROPIC_API_KEY` configurada. Configurar a variável (ver [`.env.example`](../.env.example)) é suficiente para ativar o `ClaudeAgentProvider` sem qualquer mudança de código.

### Conversa contínua

Uma sessão do `ClaudeSDKClient` mantém contexto entre chamadas de `query()`/`receive_response()` — por isso `ClaudeAgentProvider.ask()` reaproveita o mesmo client a cada mensagem em vez de reconectar. Isso é o que vai permitir, quando uma IA real estiver configurada, que "o que eu disse antes" funcione dentro da mesma execução do JARVIS. Isso **não** é persistência entre execuções — encerrar o JARVIS encerra a sessão.

### Ferramentas e permissões

Nesta versão, o agente conversa **sem nenhuma ferramenta habilitada**: `allowed_tools=[]` e `disallowed_tools=[]` no `ClaudeAgentOptions`, mais um callback `can_use_tool` que nega qualquer ferramenta como segunda camada de defesa (`_deny_all_tools` em `services/claude_agent_provider.py`). `permission_mode` nunca é `bypassPermissions`. Nada equivalente a Bash, edição/escrita de arquivos, controle do computador ou MCP está acessível ao agente.

### Configurações pessoais do Claude Code não são herdadas

`ClaudeAgentOptions(setting_sources=[])` desliga o carregamento de `~/.claude/settings.json` (usuário), `.claude/settings.json` (projeto) e `.claude/settings.local.json` (local) — o JARVIS runtime não herda silenciosamente Skills, Hooks, MCPs ou permissões pessoais configuradas no Claude Code da máquina. Todo comportamento relevante é definido explicitamente em `ClaudeAgentProvider._build_options()`.

### Preparação para permissões futuras (não implementado)

A separação `AIService` → `ClaudeAgentProvider` → `can_use_tool` já é o ponto de extensão natural para uma futura camada de permissões (READ/ACTION/DANGEROUS, ver [`tools/README.md`](../tools/README.md)) e para uma GUI pedir confirmação ("JARVIS deseja executar: [Permitir] [Negar]"). Nada disso existe ainda — `_deny_all_tools` hoje nega tudo, sem exceção.

### Streaming (preparado, não exposto)

`ClaudeSDKClient.receive_response()` já é um iterador assíncrono mensagem-a-mensagem — `ClaudeAgentProvider.ask()` apenas concatena o texto final antes de devolver ao Orchestrator. Trocar isso por streaming real (token a token, para o futuro HUD) é uma mudança isolada dentro de `claude_agent_provider.py`; não exige mexer no `Orchestrator` nem no `AIService`.

## Identidade de runtime do JARVIS

`services/runtime_identity.py` — **implementado**, separado de `CLAUDE.md`. `CLAUDE.md` orienta o Claude Code *desenvolvendo* o projeto; `runtime_identity.py` é o que o Claude sabe sobre si mesmo *sendo* o JARVIS em conversa: nome, idioma padrão (PT-BR), tom natural sem teatralidade, a instrução de não inventar informação sobre o usuário e de diferenciar fato armazenado de inferência, e os limites reais desta versão (sem ferramentas, sem memória entre sessões, sem voz/HUD).

## Memória entregue à IA

```
memory/profile.md, memory/preferences.md
    ↓
MemoryService (leitura somente-leitura)
    ↓
JarvisCore.build_memory_context()   # contexto controlado
    ↓
AIService.start(memory_context=...)
    ↓
ClaudeAgentProvider  →  system_prompt  →  Claude
```

O Core lê perfil e preferências via `MemoryService` e monta um contexto controlado, entregue **uma vez**, no `start()` da sessão (não a cada mensagem) — isso vai para o `system_prompt` da sessão, junto com a identidade de runtime. O Agent SDK **não** varre `memory/` sozinho: `setting_sources=[]` e a ausência de qualquer ferramenta de leitura de arquivo garantem isso. A memória continua somente leitura em runtime — nenhuma escrita acontece a partir de uma conversa.

## Responsabilidade de cada camada

### Usuário
Ponto de entrada de tudo: fala ou digita um pedido, e recebe respostas em texto e/ou voz.

### App / HUD — [`app/`](../app/), [`frontend/`](../frontend/)
**Status: implementado (terminal + HUD gráfico + Application Layer).**
Dois frontends hoje: o terminal (`app/terminal.py`), fino — só lê input, imprime output e trata encerramento (Ctrl+C, EOF, `/exit`) — e o HUD gráfico (`frontend/`, PySide6/QML, ver seção "Frontend/HUD" acima), os dois falando com `JarvisApplication`. O pacote `app/` também contém o núcleo de execução (`JarvisCore`, `Orchestrator`, `commands.py`, `state.py`) e a Application Layer (`application.py`, `models.py`, `conversation.py`, `status.py`, `permissions.py`). Voz **não** existe ainda.

### Orquestrador JARVIS — [`app/orchestrator.py`](../app/orchestrator.py)
**Status: implementado.**
Decide o que fazer com cada entrada: comando interno (`CommandRegistry`, síncrono) ou mensagem comum (`AIService`, assíncrono) — devolvendo o texto cru da resposta (formatação de apresentação, como o prefixo "JARVIS: ", é responsabilidade de `JarvisApplication`/terminal). Integra IA com estados (`THINKING` → `IDLE`, ou `THINKING` → `ERROR` → `IDLE` em falha) e eventos internos (`ai.request.started/completed/failed`). Não conhece `claude_agent_sdk`, nem tipos internos do SDK, nem autenticação, nem `JarvisApplication` (a dependência é de cima para baixo). Chamar ferramentas, pedir confirmação e usar subagentes/Ruflo continuam **planejados**.

### Claude Code
**Status: usado como agente de desenvolvimento (fora do runtime); arquitetura de runtime preparada via `ClaudeAgentProvider`, não ativada neste ambiente.**
Como agente de desenvolvimento, interpreta pedidos, raciocina, decide quais ferramentas/skills/subagentes usar, e produz respostas — consultando e atualizando a memória (`memory/`, `projects/`, `daily/`) conforme `CLAUDE.md`. Como motor de runtime do assistente, a integração usa o **Claude Agent SDK** (`claude-agent-sdk`, pacote Python `claude_agent_sdk`) via `ClaudeSDKClient`, não a API de mensagens tradicional.

### Serviços internos — [`services/`](../services/)
**Status: implementado.**
- `memory_service.py` — leitura somente-leitura de `memory/profile.md` e `memory/preferences.md`, com tratamento de erro para arquivo ausente/ilegível. Escrita de memória continua planejada.
- `event_bus.py` — pub/sub síncrono em memória, **interno** (`Core`/`Orchestrator`). Eventos hoje: `jarvis.started`, `jarvis.stopped`, `state.changed`, `message.received`, `message.responded`, `command.executed`, `ai.connecting`, `ai.connected`, `ai.disconnected`, `ai.request.started`, `ai.request.completed`, `ai.request.failed`, `permission.requested`, `permission.resolved`. Não confundir com o `AppEvent` de `JarvisApplication` (ver seção "Application Layer") — este é interno, aquele é o contrato estável para frontends.
- `ai_service.py` — interface `AIService` (agora com lifecycle assíncrono `start`/`ask`/`close`, mais `session_active` e `backend_name`), `UnavailableAIService`, e `create_ai_service(settings)`. Não importa o Agent SDK.
- `claude_agent_provider.py` — `ClaudeAgentProvider`: único módulo que importa `claude_agent_sdk`. Erros oficiais do SDK (`CLINotFoundError`, `CLIConnectionError`, `ProcessError`, `CLIJSONDecodeError`, `ClaudeSDKError`, e qualquer outra exceção inesperada) viram `ClaudeAgentProviderError` com mensagem segura — nunca uma exceção crua do SDK escapa para o Orchestrator.
- `runtime_identity.py` — instruções de persona do JARVIS (ver seção acima).
- Comunicação com voz — **planejado**, ainda não existe módulo.

### Ferramentas / Skills / MCP / Subagentes — [`tools/`](../tools/), [`.claude/skills/`](../.claude/skills/), [`.claude/agents/`](../.claude/agents/), [`integrations/`](../integrations/)
**Status: não implementado (planejado).**
O conjunto de capacidades que o Claude poderá invocar para agir: ferramentas locais classificadas por risco (READ / ACTION / DANGEROUS — ver [`tools/README.md`](../tools/README.md)), Skills reutilizáveis, servidores MCP para integrações externas, e subagentes especializados em tarefas específicas. Nesta v0.3, todas as ferramentas do Agent SDK estão explicitamente desligadas (ver seção "Ferramentas e permissões" acima).

### Sistema operacional / APIs / serviços
**Status: não implementado (planejado).**
A camada mais externa: o Windows em si (arquivos, processos, automações) e serviços/APIs de terceiros acessados via `integrations/`. Toda ação aqui deve passar pela classificação de risco de `tools/` antes de ser executada.

## Fluxos paralelos

- **Claude Code ↔ Memory**: `MemoryService` permite leitura somente-leitura de `memory/profile.md` e `memory/preferences.md` a partir do código (**implementado**) e alimenta o contexto entregue à IA (**implementado**, ver seção acima); o Claude Code, como agente de desenvolvimento, também lê essa memória diretamente conforme `CLAUDE.md` (**implementado**, fora do runtime do Core). Escrita programática de memória é **planejada**.
- **Hooks → App/HUD**: **planejado**.
- **Voice STT → Orquestrador**: **planejado**.
- **Orquestrador → TTS**: **planejado**.

## Sistema de permissões (ferramentas do computador)

Nenhuma ferramenta com efeito no mundo real deve ser executada sem passar pela classificação de risco descrita em [`tools/README.md`](../tools/README.md):

- **READ**: livre.
- **ACTION**: pode exigir confirmação, dependendo do impacto.
- **DANGEROUS**: sempre exige confirmação explícita do usuário.

**Status: classificação em si apenas documentada; fundação de modelo implementada.** O JARVIS não executa nenhuma ação classificável nessas categorias — não há ferramentas de computador nem MCP habilitados nesta versão (ver "Ferramentas e permissões" acima para o mecanismo específico do Agent SDK). `app/permissions.py` (`PermissionService`, `PermissionRequest`, `RiskLevel`, `PermissionStatus`) já implementa o modelo de dados e o fluxo `request → approve/deny` em memória, com eventos (`permission.requested`/`permission.resolved`) — mas **não está conectado a nenhuma ferramenta real**. Existe só para o futuro HUD conseguir mostrar "JARVIS deseja realizar uma ação — [Permitir] [Negar]" sem exigir redesenho do backend quando ferramentas reais existirem.

## Integração futura: Ruflo (multiagente)

**Status: PLANEJADO / NÃO IMPLEMENTADO.** Ruflo ([github.com/ruvnet/ruflo](https://github.com/ruvnet/ruflo)) não foi instalado, configurado ou integrado nesta etapa — apenas registrado aqui como direção arquitetural futura.

```
                    JARVIS
                       │
                 Orchestrator
                       │
          ┌────────────┴────────────┐
          ↓                         ↓
      AIService              Agent Orchestration
          ↓                         ↓
 ClaudeAgentProvider              Ruflo
          ↓                         ↓
 Claude Agent SDK          múltiplos agentes
```

Ideia futura: o `AIService`/`ClaudeAgentProvider` continua atendendo interações normais; Ruflo seria acionado pelo Orchestrator apenas para tarefas complexas que se beneficiem de múltiplos agentes trabalhando em paralelo (orquestração multiagente, divisão de tarefas, agentes especializados, workflows avançados). Ruflo **não substitui** o `AIService` e **não é obrigatório** para o JARVIS funcionar — seria uma integração opcional e desacoplada, acionada sob demanda pelo Orchestrator, não uma dependência do Core.

## Async: por que e até onde

O Claude Agent SDK é assíncrono (`ClaudeSDKClient` usa `async`/`await`). Para integrá-lo sem uma ponte sync/async artificial, `JarvisCore.start/stop/handle_input`, `Orchestrator.handle/_handle_message`, `JarvisApplication` (praticamente por inteiro) e `app/terminal.run` também são `async`. `main.py` cria **um único** event loop (`asyncio.run`) para toda a execução — não é recriado por mensagem, nem por requisição cancelada. `JarvisApplication.send_message()` envolve a chamada ao Core em uma `asyncio.Task` própria (`self._current_request_task`) especificamente para permitir cancelamento local (`Task.cancel()`) sem afetar o loop inteiro. O loop de terminal usa `input()` de forma bloqueante entre mensagens; como não há nenhuma outra tarefa concorrente disputando o loop nesta versão, isso não é um problema. Comandos internos (`/help`, `/status`, etc.) continuam síncronos, pois são instantâneos. Este design já comporta, sem reescrita: streaming (iterar `receive_response()` incrementalmente e emitir `response.delta`), uma GUI/HUD chamando `JarvisApplication` a partir do próprio loop assíncrono dela, e futuras ferramentas assíncronas.

## O que já existe vs. o que é planejamento

**IMPLEMENTADO NO v0.5:**
- HUD gráfico (`frontend/`, PySide6/QML, `python -m frontend`) — núcleo de IA animado reagindo a estado real, chat, status, cancelamento, nova conversa, overlay de permissão preparado (ver seção "Frontend/HUD" acima e [`frontend/README.md`](../frontend/README.md))
- `JarvisBridge` (`frontend/bridge.py`) — ponte fina, orientada a eventos (sem polling), entre QML e `JarvisApplication`
- Application Layer (`JarvisApplication`, `app/application.py`) — fronteira estável entre Core e qualquer frontend (terminal e HUD)
- Modelos de domínio sem dependência do Agent SDK (`app/models.py`): `Message`, `AssistantResponse`, `StatusSnapshot`, `AppEvent`, `PermissionRequest`, etc.
- Histórico de conversa em runtime (`app/conversation.py`), separado e nunca confundido com `memory/`
- Stream de eventos para consumidores externos (`subscribe()`/`unsubscribe()`/`events()`), sem WebSocket/servidor
- Status consolidado com fonte única (`app/status.py`), usado tanto por `/status` quanto por `get_status()`
- Política de concorrência (uma requisição ativa por conversa, rejeição limpa) e cancelamento (`asyncio.Task.cancel()`)
- Erros de domínio estruturados para a interface (`AppErrorCode`: `AI_UNAVAILABLE`, `JARVIS_BUSY`, `INTERNAL_ERROR`)
- "Nova conversa" (`/new`, alias `/reset` no terminal; controle "NOVA CONVERSA" no HUD): limpa histórico runtime e reinicia a sessão de IA, sem tocar `memory/`
- Fundação de permissões em memória (`app/permissions.py`), com UI de overlay pronta no HUD — não conectada a ferramentas reais
- Terminal migrado para consumir `JarvisApplication`, não mais `JarvisCore` diretamente
- Tudo o que já era v0.3/v0.4 (Claude Agent SDK, lifecycle, fallback, memória somente-leitura, estados, event bus interno)
- 105 testes automatizados, todos offline (mocks/fakes, sem chamada real, incluindo smoke test de QML offscreen)
- Core/Application/HUD funcionais sem qualquer API key configurada

**PREPARADO, MAS NÃO ATIVADO:**
- Conexão real com Claude (arquitetura pronta; falta `ANTHROPIC_API_KEY` no ambiente)
- Sessão real de conversa contínua (testada com fakes; não validada com IA real nesta etapa)
- Streaming real token-a-token (contrato de eventos já existe; falta só a extensão em `ClaudeAgentProvider.ask()` e ligar `response.delta` no HUD)
- Configuração futura de API key (`.env.example` documenta as variáveis; nenhum valor real existe no repositório)

**PLANEJADO:**
- Voz: entrada (STT) e saída (TTS) — o núcleo visual do HUD já está preparado para os estados `LISTENING`/`SPEAKING`, mas nada de áudio existe
- Permissões interativas de verdade (overlay do HUD já existe; falta conectar a ferramentas reais)
- Ferramentas de computador (READ/ACTION/DANGEROUS conectado à execução)
- MCP
- Subagentes especializados
- **Ruflo** / orquestração multiagente — inclusive uma futura representação visual ("painel AGENTS") no HUD, não implementada
- Skills em runtime
- Memória avançada (embeddings, busca semântica, memória de curto/longo prazo)
- Persistência de sessão e de conversas entre execuções
- Tela de configurações, temas alternativos, empacotamento como aplicativo Windows instalável

Qualquer trabalho futuro deve atualizar este documento se a arquitetura real divergir do que está descrito aqui.
