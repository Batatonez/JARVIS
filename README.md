# JARVIS

Assistente pessoal modular para Windows, com Claude Code como agente/cérebro principal.

## Objetivo

Construir um assistente pessoal com aplicativo próprio para Windows, capaz de conversar por texto e voz, lembrar de contexto sobre o usuário e seus projetos, e usar o Claude Code como motor de raciocínio e execução — com ferramentas, MCPs, skills, hooks e subagentes especializados, sempre sob um sistema de permissões que impede ações perigosas sem confirmação.

## Estado atual

🚧 **JARVIS Backend v0.4.** Uma Application Layer (`JarvisApplication`) estável entre o Core e qualquer frontend futuro — histórico de conversa, stream de eventos, status consolidado, cancelamento — sobre o mesmo Core assíncrono com a arquitetura do **Claude Agent SDK** pronta (`ClaudeAgentProvider`). Ainda sem interface gráfica, sem voz, sem MCP e sem automação. Neste ambiente de desenvolvimento não há API key configurada, então o comportamento observável é o fallback seguro (`UnavailableAIService`).

## Como executar

Requer Python 3.10+ (testado com 3.14).

Recomendado usar um ambiente virtual:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

(Se o PowerShell bloquear `Activate.ps1` com erro de política de execução, rode antes: `Set-ExecutionPolicy -Scope Process RemoteSigned`.)

Sem `ANTHROPIC_API_KEY` configurada, o JARVIS funciona normalmente e avisa que a IA não está configurada (não finge uma resposta). Para conversar de verdade com o Claude, defina a variável de ambiente antes de rodar (veja [`.env.example`](.env.example)):

```powershell
$env:ANTHROPIC_API_KEY = "sua-chave-aqui"
python main.py
```

Comandos disponíveis dentro do JARVIS: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit` (alias `/quit`).

## Arquitetura geral

```
Frontend (terminal hoje; HUD/voz no futuro)
  ↓
JarvisApplication          (implementado — app/application.py — a API estável para qualquer frontend)
  ↓
JarvisCore / Orchestrator   (implementado — app/core.py, app/orchestrator.py)
  ↓
Claude Code                 (arquitetura pronta via ClaudeAgentProvider/Claude Agent SDK — services/claude_agent_provider.py)
  ↓
Ferramentas / Skills / MCP / Subagentes
  ↓
Sistema operacional / APIs / serviços
```

Detalhes completos, incluindo o que está implementado vs. preparado vs. planejado (e a direção futura com Ruflo para orquestração multiagente), em [`docs/architecture.md`](docs/architecture.md). API pública da Application Layer documentada em [`docs/application-api.md`](docs/application-api.md).

## Estrutura de pastas

| Pasta | Responsabilidade |
|---|---|
| [`.claude/`](.claude/) | Configuração do Claude Code: agentes e skills do projeto |
| [`memory/`](memory/) | Memória persistente sobre o usuário (perfil, preferências) |
| [`projects/`](projects/) | Contexto persistente de projetos acompanhados pelo JARVIS |
| [`daily/`](daily/) | Registros diários (`YYYY-MM-DD.md`) |
| [`app/`](app/) | Aplicativo/núcleo principal — `JarvisApplication` (fronteira estável), terminal, `JarvisCore`, `Orchestrator`, comandos, estado (async) |
| [`services/`](services/) | Serviços internos — memória (leitura), IA (`ClaudeAgentProvider`/`UnavailableAIService`), identidade de runtime, event bus |
| [`integrations/`](integrations/) | Integrações externas (MCP, APIs, serviços de terceiros) — futuro |
| [`tools/`](tools/) | Ferramentas que o JARVIS poderá usar, classificadas por nível de risco — futuro |
| [`config/`](config/) | Configurações do JARVIS: nomes, versão, caminhos, logging (sem segredos versionados) |
| [`tests/`](tests/) | Testes automatizados (`unittest`) do Core |
| [`docs/`](docs/) | Documentação técnica e de arquitetura |

Pastas ainda não implementadas contêm um `README.md` explicando sua finalidade futura.

## Funcionalidades

**Implementado (JARVIS Backend v0.4):**
- `JarvisApplication`: API estável para qualquer frontend — `send_message`, `cancel_current_request`, `new_conversation`, `get_status`, `get_messages`, `subscribe`/`events` (ver [`docs/application-api.md`](docs/application-api.md))
- Histórico de conversa em runtime, separado da memória persistente
- Stream de eventos em processo (sem WebSocket/servidor) para um futuro HUD reagir em tempo real
- Política de concorrência clara (uma requisição por vez) e cancelamento real via `asyncio.Task`
- Erros estruturados para a interface (`AI_UNAVAILABLE`, `JARVIS_BUSY`, `INTERNAL_ERROR`) — nunca texto para analisar
- Fundação de permissões em memória (`app/permissions.py`), não conectada a ferramentas reais ainda
- Núcleo executável por terminal, assíncrono (`python main.py`), agora consumindo a Application Layer
- Comandos: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit`
- Leitura somente-leitura da memória (`profile.md`, `preferences.md`), entregue como contexto controlado à IA
- Arquitetura do **Claude Agent SDK** pronta (`ClaudeAgentProvider`, sessão contínua, ferramentas desabilitadas), condicionada a `ANTHROPIC_API_KEY`; sem a chave, fallback automático e seguro para `UnavailableAIService`

**Preparado, mas não ativado:**
- Conexão real com Claude e sessão de conversa contínua (arquitetura pronta e testada com fakes; não validada com IA real nesta etapa — sem API key neste ambiente)
- Streaming real token-a-token (contrato de eventos já existe)

**Planejado:**
- Aplicativo próprio para Windows com interface/HUD moderna (v0.5), com streaming visual
- Entrada por voz (STT) e resposta por voz (TTS)
- Permissões interativas de verdade e ferramentas para interagir com o computador (READ / ACTION / DANGEROUS)
- MCPs, Skills e Hooks do Claude Code
- Subagentes especializados
- **Ruflo** ([github.com/ruvnet/ruflo](https://github.com/ruvnet/ruflo)) como camada opcional de orquestração multiagente para tarefas complexas — não instalado, não obrigatório para o JARVIS funcionar
- Persistência de conversas entre execuções
- Integração com outras APIs e serviços externos

## Aviso

Este projeto está em desenvolvimento inicial. Boa parte das funcionalidades listadas acima ainda não existe em código — ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento completo de implementado vs. preparado vs. planejado.
