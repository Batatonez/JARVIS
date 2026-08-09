# JARVIS

Assistente pessoal modular para Windows, com Claude Code como agente/cérebro principal.

## Objetivo

Construir um assistente pessoal com aplicativo próprio para Windows, capaz de conversar por texto e voz, lembrar de contexto sobre o usuário e seus projetos, e usar o Claude Code como motor de raciocínio e execução — com ferramentas, MCPs, skills, hooks e subagentes especializados, sempre sob um sistema de permissões que impede ações perigosas sem confirmação.

## Estado atual

🚧 **Fundação.** Este repositório contém apenas a estrutura base do projeto: organização de pastas, documentação de arquitetura e memória inicial em Markdown. Nenhuma dependência foi instalada e nenhuma funcionalidade (voz, HUD, MCP, automação, integrações) foi implementada ainda.

## Arquitetura geral

```
Usuário
  ↓
App / HUD                (interface — ainda não implementada)
  ↓
Orquestrador JARVIS       (ainda não implementado)
  ↓
Claude Code                (agente/cérebro)
  ↓
Ferramentas / Skills / MCP / Subagentes
  ↓
Sistema operacional / APIs / serviços
```

Detalhes completos em [`docs/architecture.md`](docs/architecture.md).

## Estrutura de pastas

| Pasta | Responsabilidade |
|---|---|
| [`.claude/`](.claude/) | Configuração do Claude Code: agentes e skills do projeto |
| [`memory/`](memory/) | Memória persistente sobre o usuário (perfil, preferências) |
| [`projects/`](projects/) | Contexto persistente de projetos acompanhados pelo JARVIS |
| [`daily/`](daily/) | Registros diários (`YYYY-MM-DD.md`) |
| [`app/`](app/) | Aplicativo/interface principal (futuro) |
| [`services/`](services/) | Serviços internos (comunicação com Claude, memória, voz, eventos, estado) |
| [`integrations/`](integrations/) | Integrações externas (MCP, APIs, serviços de terceiros) |
| [`tools/`](tools/) | Ferramentas que o JARVIS poderá usar, classificadas por nível de risco |
| [`config/`](config/) | Configurações do JARVIS (sem segredos versionados) |
| [`docs/`](docs/) | Documentação técnica e de arquitetura |

Cada pasta ainda não implementada contém um `README.md` explicando sua finalidade futura.

## Funcionalidades planejadas

- Aplicativo próprio para Windows com interface/HUD moderna
- Chat por texto
- Entrada por voz (STT) e resposta por voz (TTS)
- Claude Code como agente principal de raciocínio e execução
- Memória persistente (perfil, preferências, projetos, histórico diário)
- Ferramentas para interagir com o computador, com sistema de permissões (READ / ACTION / DANGEROUS)
- MCPs, Skills e Hooks do Claude Code
- Subagentes especializados
- Integração com APIs e serviços externos
- Arquitetura modular, permitindo evolução sem reescrita

## Aviso

Este projeto ainda está na fundação. As pastas e documentos aqui descrevem a arquitetura **planejada** — a maior parte das funcionalidades listadas acima ainda não existe em código.
