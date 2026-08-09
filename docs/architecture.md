# Arquitetura do JARVIS

> **Aviso:** este documento mistura o que já existe (**IMPLEMENTADO**) com o que ainda é planejamento (**PLANEJADO**). Cada seção diz explicitamente qual é o caso. Não presuma que algo existe só porque está no diagrama — o diagrama descreve a arquitetura-alvo completa, não o estado atual.

## Estado atual em uma frase

**JARVIS Core v0.2**: um núcleo executável por terminal, com IA real (Claude) conectável via `ANTHROPIC_API_KEY` — sem essa variável, cai automaticamente para o placeholder anterior. Ainda sem interface gráfica, sem voz, sem MCP e sem automação.

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

## Como isso mapeia para o código hoje (v0.2)

Como ainda não existe HUD, "App/HUD" e "Orquestrador" desta versão são realizados dentro do mesmo pacote `app/`, com uma interface de terminal como única apresentação:

```
main.py
  ↓
app/terminal.py        (apresentação: loop de input/print no terminal)
  ↓
app/core.py             (JarvisCore: fachada, estado, ciclo de vida)
  ↓
app/orchestrator.py     (Orchestrator: comando vs. mensagem)
  ↓
app/commands.py         (comandos internos: /help /status /memory /clear /exit)
  ↓
services/                (memory_service, ai_service + create_ai_service, claude_provider, event_bus)
```

Isso é uma **implementação parcial e provisória** da camada "App/HUD → Orquestrador" do diagrama acima — não uma arquitetura diferente. Quando o HUD gráfico for criado, ele deve reutilizar `JarvisCore`/`Orchestrator` sem duplicar lógica, trocando apenas `app/terminal.py` por uma interface gráfica (ou somando as duas). Quando a IA for conectada, isso acontece dentro de `services/ai_service.py`, sem precisar mexer no Orchestrator.

## Responsabilidade de cada camada

### Usuário
Ponto de entrada de tudo: fala ou digita um pedido, e recebe respostas em texto e/ou voz.

### App / HUD — [`app/`](../app/)
**Status: implementado parcialmente (v0.1, terminal).**
Hoje é uma interface de terminal (`app/terminal.py`) fina — só lê input, imprime output e trata encerramento (Ctrl+C, EOF, `/exit`). O pacote `app/` também contém o núcleo de execução (`JarvisCore` em `core.py`, `Orchestrator` em `orchestrator.py`, comandos em `commands.py`, estado em `state.py`), que não são "apresentação" propriamente, mas ficam aqui por serem a aplicação em si — a parte que será reaproveitada quando o HUD gráfico existir. HUD visual, voz e integração real com IA **não** existem ainda.

### Orquestrador JARVIS — [`app/orchestrator.py`](../app/orchestrator.py)
**Status: implementado (versão inicial, v0.1).**
Decide o que fazer com cada entrada: comando interno (`CommandRegistry`) ou mensagem comum. Mensagens comuns ainda não têm IA conectada — a resposta deixa isso explícito em vez de fingir uma resposta inteligente. Consulta `MemoryService` e `AIService`, e emite eventos via `EventBus` a cada mensagem recebida/respondida. As decisões futuras de quando consultar memória de forma ativa, quando chamar uma ferramenta, quando pedir confirmação, quando gerar eventos para o HUD e quando usar subagentes ainda são **planejadas** — nesta v0.1 o roteamento é só "comando ou mensagem".

### Claude Code
**Status: usado como agente de desenvolvimento (fora do runtime); conectável em tempo de execução como `ClaudeProvider` desde a v0.2, condicionado a `ANTHROPIC_API_KEY`.**
Como agente de desenvolvimento, interpreta pedidos, raciocina, decide quais ferramentas/skills/subagentes usar, e produz respostas — consultando e atualizando a memória (`memory/`, `projects/`, `daily/`) conforme `CLAUDE.md`. Como motor de runtime do assistente (`services/claude_provider.py`), hoje ele só conversa: recebe uma mensagem do usuário via `Orchestrator` → `AIService`, chama a API da Anthropic com um system prompt simples explicando o que o JARVIS é/não é ainda, e devolve texto. Não tem ferramentas, não tem memória de conversa, não decide nada sozinho — isso é planejamento futuro.

### Serviços internos — [`services/`](../services/)
**Status: implementado (v0.2).**
- `memory_service.py` — **implementado.** Leitura somente-leitura de `memory/profile.md` e `memory/preferences.md`, com tratamento de erro para arquivo ausente/ilegível. Escrita de memória é planejada, com regras próprias.
- `event_bus.py` — **implementado.** Pub/sub síncrono em memória (`jarvis.started`, `jarvis.stopped`, `state.changed`, `message.received`, `message.responded`, `command.executed`). Sem fila, sem rede — pronto para o HUD se inscrever nesses eventos no futuro.
- `ai_service.py` — **implementado.** Define a interface `AIService`, o placeholder `UnavailableAIService`, e `create_ai_service(settings)` — a factory que escolhe entre os dois. Não importa a SDK da Anthropic.
- `claude_provider.py` — **implementado.** `ClaudeProvider`: provider real usando a SDK oficial `anthropic`. Único módulo do projeto que fala com a API da Anthropic; erros de rede/API viram `ClaudeProviderError` com mensagem segura (sem segredos), nunca uma exceção crua.
- Comunicação com voz — **planejado**, ainda não existe módulo.

### Ferramentas / Skills / MCP / Subagentes — [`tools/`](../tools/), [`.claude/skills/`](../.claude/skills/), [`.claude/agents/`](../.claude/agents/), [`integrations/`](../integrations/)
**Status: não implementado (planejado).**
O conjunto de capacidades que o Claude Code poderá invocar para agir: ferramentas locais classificadas por risco (READ / ACTION / DANGEROUS — ver [`tools/README.md`](../tools/README.md)), Skills reutilizáveis, servidores MCP para integrações externas, e subagentes especializados em tarefas específicas.

### Sistema operacional / APIs / serviços
**Status: não implementado (planejado).**
A camada mais externa: o Windows em si (arquivos, processos, automações) e serviços/APIs de terceiros acessados via `integrations/`. Toda ação aqui deve passar pela classificação de risco de `tools/` antes de ser executada.

## Fluxos paralelos

- **Claude Code ↔ Memory**: hoje, `MemoryService` já permite leitura somente-leitura de `memory/profile.md` e `memory/preferences.md` a partir do código (**implementado**); o Claude Code, como agente de desenvolvimento, também lê essa memória diretamente conforme `CLAUDE.md` (**implementado**, fora do runtime do Core). Escrita programática de memória e consulta ativa por parte do Orchestrator são **planejadas**.
- **Hooks → App/HUD**: **planejado**.
- **Voice STT → Orquestrador**: **planejado**.
- **Orquestrador → TTS**: **planejado**.

## Sistema de permissões

Nenhuma ferramenta com efeito no mundo real deve ser executada sem passar pela classificação de risco descrita em [`tools/README.md`](../tools/README.md):

- **READ**: livre.
- **ACTION**: pode exigir confirmação, dependendo do impacto.
- **DANGEROUS**: sempre exige confirmação explícita do usuário.

**Status: apenas documentado, não implementado em código.** Esta v0.1 não executa nenhuma ação classificável nessas categorias — não há ferramentas de computador ainda.

## O que já existe vs. o que é planejamento

**IMPLEMENTADO (JARVIS Core v0.2):**
- Núcleo executável por terminal (`main.py`, `app/terminal.py`)
- Orquestração básica: comando vs. mensagem (`app/orchestrator.py`)
- `JarvisCore`: ciclo de vida e estado (`app/core.py`, `app/state.py`)
- Comandos internos: `/help`, `/status`, `/memory`, `/clear`, `/exit` (`app/commands.py`)
- Leitura somente-leitura de memória (`services/memory_service.py`)
- Event bus interno em memória (`services/event_bus.py`)
- Conexão real com IA via `ClaudeProvider` (`services/claude_provider.py`), condicionada a `ANTHROPIC_API_KEY`; sem a chave, cai automaticamente para `UnavailableAIService` (`services/ai_service.py`)
- Configuração central e logging (`config/`)

**PLANEJADO (ainda não implementado):**
- Aplicativo/HUD gráfico
- Voz: entrada (STT) e saída (TTS)
- MCP
- Ferramentas de computador e sistema de permissões em código (READ/ACTION/DANGEROUS)
- Subagentes especializados
- Automações do Windows
- Integrações externas (APIs, serviços de terceiros)
- Escrita programática de memória

Qualquer trabalho futuro deve atualizar este documento se a arquitetura real divergir do que está descrito aqui.
