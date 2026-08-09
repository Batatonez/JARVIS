# Arquitetura do JARVIS

> **Aviso:** este documento mistura o que já existe (**IMPLEMENTADO**) com o que ainda é planejamento (**PLANEJADO**). Cada seção diz explicitamente qual é o caso. Não presuma que algo existe só porque está no diagrama — o diagrama descreve a arquitetura-alvo completa, não o estado atual.

## Estado atual em uma frase

**JARVIS Core v0.1**: um núcleo executável por terminal, sem IA conectada, sem interface gráfica, sem voz, sem MCP e sem automação — a base sobre a qual as próximas camadas serão construídas sem reescrita.

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

## Como isso mapeia para o código hoje (v0.1)

Como ainda não existe HUD nem IA conectada, "App/HUD" e "Orquestrador" desta v0.1 são realizados dentro do mesmo pacote `app/`, com uma interface de terminal como única apresentação:

```
main.py
  ↓
app/terminal.py        (apresentação: loop de input/print no terminal)
  ↓
app/core.py             (JarvisCore: fachada, estado, ciclo de vida)
  ↓
app/orchestrator.py     (Orchestrator: comando vs. mensagem, ainda sem IA)
  ↓
app/commands.py         (comandos internos: /help /status /memory /clear /exit)
  ↓
services/                (memory_service, ai_service, event_bus)
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
**Status: já é usado como agente de desenvolvimento; ainda não integrado como motor em tempo de execução do assistente (planejado).**
O agente/cérebro do sistema. Interpreta pedidos, raciocina, decide quais ferramentas/skills/subagentes usar, e produz respostas. Consulta e atualiza a memória (`memory/`, `projects/`, `daily/`) conforme as regras definidas em [`CLAUDE.md`](../CLAUDE.md). Quando conectado, isso acontecerá através de um provider concreto de `services/ai_service.py` (ex.: `ClaudeProvider`), sem alterar o Orchestrator.

### Serviços internos — [`services/`](../services/)
**Status: implementado parcialmente (v0.1).**
- `memory_service.py` — **implementado.** Leitura somente-leitura de `memory/profile.md` e `memory/preferences.md`, com tratamento de erro para arquivo ausente/ilegível. Escrita de memória é planejada, com regras próprias.
- `event_bus.py` — **implementado.** Pub/sub síncrono em memória (`jarvis.started`, `jarvis.stopped`, `state.changed`, `message.received`, `message.responded`, `command.executed`). Sem fila, sem rede — pronto para o HUD se inscrever nesses eventos no futuro.
- `ai_service.py` — **abstração implementada, provider real planejado.** Define a interface `AIService` e o placeholder `UnavailableAIService`. Nenhuma chamada de IA acontece nesta versão.
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

**IMPLEMENTADO (JARVIS Core v0.1):**
- Núcleo executável por terminal (`main.py`, `app/terminal.py`)
- Orquestração básica: comando vs. mensagem (`app/orchestrator.py`)
- `JarvisCore`: ciclo de vida e estado (`app/core.py`, `app/state.py`)
- Comandos internos: `/help`, `/status`, `/memory`, `/clear`, `/exit` (`app/commands.py`)
- Leitura somente-leitura de memória (`services/memory_service.py`)
- Event bus interno em memória (`services/event_bus.py`)
- Abstração de IA ainda indisponível (`services/ai_service.py` — `UnavailableAIService`)
- Configuração central e logging (`config/`)

**PLANEJADO (ainda não implementado):**
- Conexão real com IA (Claude como provider)
- Aplicativo/HUD gráfico
- Voz: entrada (STT) e saída (TTS)
- MCP
- Ferramentas de computador e sistema de permissões em código (READ/ACTION/DANGEROUS)
- Subagentes especializados
- Automações do Windows
- Integrações externas (APIs, serviços de terceiros)
- Escrita programática de memória

Qualquer trabalho futuro deve atualizar este documento se a arquitetura real divergir do que está descrito aqui.
