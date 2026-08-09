# Arquitetura do JARVIS

> **Aviso:** este documento descreve a arquitetura **planejada** para o JARVIS. Neste momento, o projeto está na fase de fundação — apenas estrutura de pastas, documentação e memória em Markdown existem. Nenhuma das camadas abaixo (App/HUD, Orquestrador, ferramentas, voz, MCP) está implementada. Onde algo já existe de fato, este documento diz isso explicitamente; o resto é planejamento.

## Visão geral

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

## Responsabilidade de cada camada

### Usuário
Ponto de entrada de tudo: fala ou digita um pedido, e recebe respostas em texto e/ou voz.

### App / HUD — [`app/`](../app/)
**Status: não implementado.**
Camada de apresentação. Renderiza a interface (chat de texto, HUD visual), captura entrada de texto e voz, e exibe/reproduz as respostas. Não contém lógica de negócio nem memória — apenas repassa entrada ao Orquestrador e apresenta o que ele devolve.

### Orquestrador JARVIS — [`services/`](../services/)
**Status: não implementado.**
Camada intermediária entre o App e o Claude Code. Responsável por: gerenciar o estado da sessão, decidir quando consultar memória, encaminhar pedidos ao Claude Code, e coordenar hooks e eventos entre as demais camadas. É o "sistema nervoso" do JARVIS — não é o agente de raciocínio em si (isso é o Claude Code), mas o que conecta as partes.

### Claude Code
**Status: já é usado como agente de desenvolvimento; ainda não integrado como motor em tempo de execução do assistente.**
O agente/cérebro do sistema. Interpreta pedidos, raciocina, decide quais ferramentas/skills/subagentes usar, e produz respostas. Consulta e atualiza a memória (`memory/`, `projects/`, `daily/`) conforme as regras definidas em [`CLAUDE.md`](../CLAUDE.md).

### Ferramentas / Skills / MCP / Subagentes — [`tools/`](../tools/), [`.claude/skills/`](../.claude/skills/), [`.claude/agents/`](../.claude/agents/), [`integrations/`](../integrations/)
**Status: não implementado.**
O conjunto de capacidades que o Claude Code pode invocar para agir: ferramentas locais classificadas por risco (READ / ACTION / DANGEROUS — ver [`tools/README.md`](../tools/README.md)), Skills reutilizáveis, servidores MCP para integrações externas, e subagentes especializados em tarefas específicas.

### Sistema operacional / APIs / serviços
**Status: não implementado.**
A camada mais externa: o Windows em si (arquivos, processos, automações) e serviços/APIs de terceiros acessados via `integrations/`. Toda ação aqui deve passar pela classificação de risco de `tools/` antes de ser executada.

## Fluxos paralelos

- **Claude Code ↔ Memory**: o agente lê `memory/`, `projects/` e `daily/` antes de responder quando o contexto pessoal é relevante, e escreve nessas pastas apenas quando há informação com utilidade futura clara (nunca automaticamente).
- **Hooks → App/HUD**: eventos do Claude Code (hooks) poderão notificar a interface sobre o que está acontecendo (ex.: "ferramenta X está rodando").
- **Voice STT → Orquestrador**: entrada de voz é transcrita e entra no mesmo fluxo que uma mensagem de texto.
- **Orquestrador → TTS**: respostas podem ser convertidas em voz antes de chegar ao usuário.

## Sistema de permissões

Nenhuma ferramenta com efeito no mundo real deve ser executada sem passar pela classificação de risco descrita em [`tools/README.md`](../tools/README.md):

- **READ**: livre.
- **ACTION**: pode exigir confirmação, dependendo do impacto.
- **DANGEROUS**: sempre exige confirmação explícita do usuário.

Essa regra é transversal a todas as camadas acima — App, Orquestrador e Claude Code devem respeitá-la.

## O que ainda é apenas planejamento

Para deixar isso inequívoco: **todas** as camadas de App/HUD, Orquestrador, ferramentas, MCP, subagentes especializados, integrações externas, voz (STT/TTS) e sistema de permissões automatizado são planejamento futuro. A única coisa que existe hoje é a estrutura de pastas, a documentação e a memória em Markdown. Qualquer trabalho futuro deve atualizar este documento se a arquitetura real divergir do que está descrito aqui.
