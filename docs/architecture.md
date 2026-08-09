# Arquitetura do JARVIS

> **Aviso:** este documento mistura o que já existe (**IMPLEMENTADO**) com o que está **PREPARADO, MAS NÃO ATIVADO** e o que é puro **PLANEJADO**. Cada seção diz explicitamente qual é o caso. Não presuma que algo existe só porque está no diagrama — o diagrama descreve a arquitetura-alvo completa, não o estado atual.

## Estado atual em uma frase

**JARVIS Core v0.3**: um núcleo executável por terminal, assíncrono, com a arquitetura do **Claude Agent SDK** pronta para uma sessão de conversa contínua via `ClaudeAgentProvider` — mas sem nenhuma API key configurada neste ambiente de desenvolvimento. O comportamento padrão observável hoje é o fallback seguro (`UnavailableAIService`): o JARVIS inicia, responde comandos e avisa que a IA não está configurada, sem travar e sem pedir credencial.

## Visão geral (arquitetura-alvo, planejada)

```
Usuário
  ↓
App / HUD
  ↓
Orquestrador JARVIS
  ↓
Claude Code
  ↓
Ferramentas / Skills / MCP / Subagentes
  ↓
Sistema operacional / APIs / serviços
```

Em paralelo a esse fluxo principal:

```
Claude Code  ↔  Memory
Hooks        →  App / HUD
Voice STT    →  Orquestrador
Orquestrador →  TTS
```

## Como isso mapeia para o código hoje (v0.3)

Como ainda não existe HUD, "App/HUD" e "Orquestrador" desta versão são realizados dentro do mesmo pacote `app/`, com uma interface de terminal como única apresentação. Todo o caminho de mensagem comum é assíncrono (`asyncio`), com um único event loop criado em `main.py`:

```
main.py  →  asyncio.run(...)
  ↓
app/terminal.py        (apresentação: loop de input/print no terminal — async)
  ↓
app/core.py             (JarvisCore: fachada, estado, ciclo de vida — async)
  ↓
app/orchestrator.py     (Orchestrator: comando vs. mensagem — async)
  ↓
app/commands.py         (comandos internos, síncronos: /help /status /memory /clear /exit)
  ↓
services/                (memory_service, ai_service + create_ai_service, claude_agent_provider, runtime_identity, event_bus)
```

Isso é uma **implementação parcial e provisória** da camada "App/HUD → Orquestrador" do diagrama acima — não uma arquitetura diferente. Quando o HUD gráfico for criado, ele deve reutilizar `JarvisCore`/`Orchestrator` sem duplicar lógica, trocando apenas `app/terminal.py` por uma interface gráfica (ou somando as duas): nenhuma delas conhece `claude_agent_sdk` diretamente.

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
JarvisCore._build_memory_context()   # contexto controlado
    ↓
AIService.start(memory_context=...)
    ↓
ClaudeAgentProvider  →  system_prompt  →  Claude
```

O Core lê perfil e preferências via `MemoryService` e monta um contexto controlado, entregue **uma vez**, no `start()` da sessão (não a cada mensagem) — isso vai para o `system_prompt` da sessão, junto com a identidade de runtime. O Agent SDK **não** varre `memory/` sozinho: `setting_sources=[]` e a ausência de qualquer ferramenta de leitura de arquivo garantem isso. A memória continua somente leitura em runtime — nenhuma escrita acontece a partir de uma conversa.

## Responsabilidade de cada camada

### Usuário
Ponto de entrada de tudo: fala ou digita um pedido, e recebe respostas em texto e/ou voz.

### App / HUD — [`app/`](../app/)
**Status: implementado parcialmente (terminal).**
Hoje é uma interface de terminal (`app/terminal.py`) fina — só lê input, imprime output e trata encerramento (Ctrl+C, EOF, `/exit`), rodando dentro do event loop único criado por `main.py`. O pacote `app/` também contém o núcleo de execução (`JarvisCore`, `Orchestrator`, `commands.py`, `state.py`), que fica aqui por ser a aplicação em si — a parte que será reaproveitada quando o HUD gráfico existir. HUD visual e voz **não** existem ainda.

### Orquestrador JARVIS — [`app/orchestrator.py`](../app/orchestrator.py)
**Status: implementado.**
Decide o que fazer com cada entrada: comando interno (`CommandRegistry`, síncrono) ou mensagem comum (`AIService`, assíncrono). Integra IA com estados (`THINKING` → `IDLE`, ou `THINKING` → `ERROR` → `IDLE` em falha) e eventos (`ai.request.started/completed/failed`). Não conhece `claude_agent_sdk`, nem tipos internos do SDK, nem autenticação. Consultar memória ativamente por conversa, chamar ferramentas, pedir confirmação e usar subagentes/Ruflo continuam **planejados**.

### Claude Code
**Status: usado como agente de desenvolvimento (fora do runtime); arquitetura de runtime preparada via `ClaudeAgentProvider`, não ativada neste ambiente.**
Como agente de desenvolvimento, interpreta pedidos, raciocina, decide quais ferramentas/skills/subagentes usar, e produz respostas — consultando e atualizando a memória (`memory/`, `projects/`, `daily/`) conforme `CLAUDE.md`. Como motor de runtime do assistente, a integração usa o **Claude Agent SDK** (`claude-agent-sdk`, pacote Python `claude_agent_sdk`) via `ClaudeSDKClient`, não a API de mensagens tradicional.

### Serviços internos — [`services/`](../services/)
**Status: implementado.**
- `memory_service.py` — leitura somente-leitura de `memory/profile.md` e `memory/preferences.md`, com tratamento de erro para arquivo ausente/ilegível. Escrita de memória continua planejada.
- `event_bus.py` — pub/sub síncrono em memória. Eventos hoje: `jarvis.started`, `jarvis.stopped`, `state.changed`, `message.received`, `message.responded`, `command.executed`, `ai.connecting`, `ai.connected`, `ai.disconnected`, `ai.request.started`, `ai.request.completed`, `ai.request.failed`.
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

**Status: apenas documentado, não implementado em código.** O JARVIS não executa nenhuma ação classificável nessas categorias — não há ferramentas de computador nem MCP habilitados nesta versão (ver "Ferramentas e permissões" acima para o mecanismo específico do Agent SDK).

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

O Claude Agent SDK é assíncrono (`ClaudeSDKClient` usa `async`/`await`). Para integrá-lo sem uma ponte sync/async artificial, `JarvisCore.start/stop/handle_input`, `Orchestrator.handle/_handle_message` e `app/terminal.run` também são `async`. `main.py` cria **um único** event loop (`asyncio.run`) para toda a execução — não é recriado por mensagem. O loop de terminal usa `input()` de forma bloqueante entre mensagens; como não há nenhuma outra tarefa concorrente rodando nesta versão, isso não é um problema. Comandos internos (`/help`, `/status`, etc.) continuam síncronos, pois são instantâneos. Este design já comporta, sem reescrita: streaming (iterar `receive_response()` incrementalmente), uma GUI/HUD chamando `JarvisCore` a partir do próprio loop assíncrono dela, e futuras ferramentas assíncronas.

## O que já existe vs. o que é planejamento

**IMPLEMENTADO NO v0.3:**
- Arquitetura do Claude Agent SDK (`ClaudeAgentProvider`, `services/claude_agent_provider.py`)
- Lifecycle explícito (`start`/`ask`/`close`), idempotente, com fallback seguro em caso de falha de conexão
- Preparação para sessão contínua de conversa (mesmo `ClaudeSDKClient` reaproveitado entre mensagens)
- Integração da memória com o contexto da IA (`MemoryService` → `JarvisCore._build_memory_context` → `AIService.start`)
- `UnavailableAIService` como fallback padrão sem API key
- Tratamento de erros oficiais do Agent SDK, convertidos para `ClaudeAgentProviderError` sem vazar segredos
- Eventos de IA no `EventBus` (`ai.connecting/connected/disconnected`, `ai.request.started/completed/failed`)
- Integração com estados existentes (`THINKING`, `ERROR`, `IDLE`)
- 48 testes automatizados, todos offline (mocks/fakes, sem chamada real)
- Core funcional sem qualquer API key configurada

**PREPARADO, MAS NÃO ATIVADO:**
- Conexão real com Claude (arquitetura pronta; falta `ANTHROPIC_API_KEY` no ambiente)
- Sessão real de conversa contínua (testada com fakes; não validada com IA real nesta etapa)
- Configuração futura de API key (`.env.example` documenta as variáveis; nenhum valor real existe no repositório)

**PLANEJADO:**
- Aplicativo/HUD gráfico
- Streaming visual da resposta
- Voz: entrada (STT) e saída (TTS)
- Permissões interativas (GUI perguntando "Permitir/Negar")
- Ferramentas de computador e sistema de permissões em código (READ/ACTION/DANGEROUS)
- MCP
- Subagentes especializados
- Skills em runtime
- Memória avançada (embeddings, busca semântica, memória de curto/longo prazo)
- Persistência de sessão entre execuções
- Ruflo / orquestração multiagente

Qualquer trabalho futuro deve atualizar este documento se a arquitetura real divergir do que está descrito aqui.
