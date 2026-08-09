# JARVIS

Assistente pessoal modular para Windows, com Claude Code como agente/cérebro principal.

## Objetivo

Construir um assistente pessoal com aplicativo próprio para Windows, capaz de conversar por texto e voz, lembrar de contexto sobre o usuário e seus projetos, e usar o Claude Code como motor de raciocínio e execução — com ferramentas, MCPs, skills, hooks e subagentes especializados, sempre sob um sistema de permissões que impede ações perigosas sem confirmação.

## Estado atual

🚧 **JARVIS Core v0.1.** Fundação completa (pastas, documentação, memória em Markdown) mais um núcleo executável por terminal, sem IA conectada, sem interface gráfica, sem voz, MCP ou automação. Nenhuma dependência externa foi instalada — tudo usa apenas a biblioteca padrão do Python.

## Como executar

Requer Python 3 (testado com 3.14) e nenhuma dependência externa.

```bash
python main.py
```

Comandos disponíveis dentro do JARVIS: `/help`, `/status`, `/memory`, `/clear`, `/exit` (alias `/quit`). Mensagens comuns ainda não são respondidas por IA — o sistema deixa isso explícito em vez de fingir uma resposta.

## Arquitetura geral

```
Usuário
  ↓
App / HUD                (v0.1: terminal — app/terminal.py)
  ↓
Orquestrador JARVIS       (implementado — app/orchestrator.py)
  ↓
Claude Code                (agente/cérebro — ainda não conectado ao runtime)
  ↓
Ferramentas / Skills / MCP / Subagentes
  ↓
Sistema operacional / APIs / serviços
```

Detalhes completos, incluindo o que está implementado vs. planejado, em [`docs/architecture.md`](docs/architecture.md).

## Estrutura de pastas

| Pasta | Responsabilidade |
|---|---|
| [`.claude/`](.claude/) | Configuração do Claude Code: agentes e skills do projeto |
| [`memory/`](memory/) | Memória persistente sobre o usuário (perfil, preferências) |
| [`projects/`](projects/) | Contexto persistente de projetos acompanhados pelo JARVIS |
| [`daily/`](daily/) | Registros diários (`YYYY-MM-DD.md`) |
| [`app/`](app/) | Aplicativo/núcleo principal — v0.1: terminal, `JarvisCore`, `Orchestrator`, comandos, estado |
| [`services/`](services/) | Serviços internos — v0.1: memória (leitura), abstração de IA, event bus |
| [`integrations/`](integrations/) | Integrações externas (MCP, APIs, serviços de terceiros) — futuro |
| [`tools/`](tools/) | Ferramentas que o JARVIS poderá usar, classificadas por nível de risco — futuro |
| [`config/`](config/) | Configurações do JARVIS: nomes, versão, caminhos, logging (sem segredos versionados) |
| [`tests/`](tests/) | Testes automatizados (`unittest`) do Core |
| [`docs/`](docs/) | Documentação técnica e de arquitetura |

Pastas ainda não implementadas contêm um `README.md` explicando sua finalidade futura.

## Funcionalidades

**Implementado (JARVIS Core v0.1):**
- Núcleo executável por terminal (`python main.py`)
- Orquestração básica (comando interno vs. mensagem comum)
- Comandos: `/help`, `/status`, `/memory`, `/clear`, `/exit`
- Leitura somente-leitura da memória (`profile.md`, `preferences.md`)
- Event bus interno e estados básicos (`IDLE`, `THINKING`, `WORKING`, ...)
- Abstração de IA ainda indisponível, pronta para receber um provider real

**Planejado:**
- Aplicativo próprio para Windows com interface/HUD moderna
- Entrada por voz (STT) e resposta por voz (TTS)
- Claude Code conectado como motor de raciocínio em tempo real
- Ferramentas para interagir com o computador, com sistema de permissões (READ / ACTION / DANGEROUS)
- MCPs, Skills e Hooks do Claude Code
- Subagentes especializados
- Integração com APIs e serviços externos

## Aviso

Este projeto está em desenvolvimento inicial. Boa parte das funcionalidades listadas acima ainda não existe em código — ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento completo de implementado vs. planejado.
