# JARVIS

Assistente pessoal modular para Windows, com Claude Code como agente/cérebro principal.

## Objetivo

Construir um assistente pessoal com aplicativo próprio para Windows, capaz de conversar por texto e voz, lembrar de contexto sobre o usuário e seus projetos, e usar o Claude Code como motor de raciocínio e execução — com ferramentas, MCPs, skills, hooks e subagentes especializados, sempre sob um sistema de permissões que impede ações perigosas sem confirmação.

## Estado atual

🚧 **JARVIS v0.8 — HUD Overhaul / UX 2.0.** Mesma arquitetura e mesmas capabilities do v0.7 (Core/Application Layer, chat, voz push-to-talk, permissões) — esta versão é só refinamento profundo do HUD: design system consolidado em `Theme.qml`, núcleo de IA v3 (mais camadas, mais profundidade, transições orgânicas entre estados), layout que escala em monitores grandes, boot sequence mais elaborado (~1,3s), e acabamento visual em praticamente todo componente (chat, input, status, title bar, overlay de permissão). Nenhuma capability nova: Claude continua sem API key/sem chamada real, Ruflo/MCP/tools continuam fora, voz continua Vosk (STT, requer modelo baixado manualmente) + SAPI5 (TTS, já funciona). Ver [`frontend/README.md`](frontend/README.md) para o design system e o estado visual por estado.

## Como executar

Requer Python 3.10+ (testado com 3.14).

Recomendado usar um ambiente virtual:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

(Se o PowerShell bloquear `Activate.ps1` com erro de política de execução, rode antes: `Set-ExecutionPolicy -Scope Process RemoteSigned`.)

**Terminal (CLI):**

```powershell
python main.py
```

**HUD gráfico:**

```powershell
python -m frontend
```

Sem `ANTHROPIC_API_KEY` configurada, o JARVIS (em qualquer um dos dois) funciona normalmente e avisa que a IA não está configurada (não finge uma resposta). Para conversar de verdade com o Claude, defina a variável de ambiente antes de rodar (veja [`.env.example`](.env.example)):

```powershell
$env:ANTHROPIC_API_KEY = "sua-chave-aqui"
python main.py        # ou: python -m frontend
```

Comandos disponíveis no terminal: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit` (alias `/quit`). No HUD, os mesmos conceitos existem como controles visuais (ver [`frontend/README.md`](frontend/README.md)).

**Voz (opcional, só no HUD):** `requirements.txt` já inclui as dependências de voz (`vosk`, `sounddevice`, `pyttsx3` — nenhuma exige GPU). A síntese de fala (TTS) funciona assim que essas dependências estiverem instaladas, usando vozes já existentes no Windows. O reconhecimento de fala (STT) exige, além disso, baixar manualmente um modelo Vosk (~50 MB, nunca feito automaticamente pelo JARVIS) — passo a passo em [`frontend/README.md`](frontend/README.md#voz-v07--push-to-talk). Sem o modelo, o botão de microfone do HUD fica desabilitado e o resto do JARVIS funciona normalmente por texto.

## Arquitetura geral

```
JARVIS HUD (PySide6/QML) ou Terminal
  ↓
JarvisApplication          (implementado — app/application.py — a API estável para qualquer frontend)
  ├→ VoiceService           (implementado — v0.7 — services/voice_service.py — STT/TTS, só usado pelo HUD)
  ↓
JarvisCore / Orchestrator   (implementado — app/core.py, app/orchestrator.py)
  ↓
Claude Code                 (arquitetura pronta via ClaudeAgentProvider/Claude Agent SDK — services/claude_agent_provider.py)
  ↓
Ferramentas / Skills / MCP / Subagentes
  ↓
Sistema operacional / APIs / serviços
```

Detalhes completos, incluindo o que está implementado vs. preparado vs. planejado (e a direção futura com Ruflo para orquestração multiagente), em [`docs/architecture.md`](docs/architecture.md). API pública da Application Layer em [`docs/application-api.md`](docs/application-api.md); arquitetura do HUD em [`frontend/README.md`](frontend/README.md).

## Estrutura de pastas

| Pasta | Responsabilidade |
|---|---|
| [`.claude/`](.claude/) | Configuração do Claude Code: agentes e skills do projeto |
| [`memory/`](memory/) | Memória persistente sobre o usuário (perfil, preferências) |
| [`projects/`](projects/) | Contexto persistente de projetos acompanhados pelo JARVIS |
| [`daily/`](daily/) | Registros diários (`YYYY-MM-DD.md`) |
| [`app/`](app/) | Aplicativo/núcleo principal — `JarvisApplication` (fronteira estável), terminal, `JarvisCore`, `Orchestrator`, comandos, estado (async) |
| [`services/`](services/) | Serviços internos — memória (leitura), IA (`ClaudeAgentProvider`/`UnavailableAIService`), identidade de runtime, event bus |
| [`frontend/`](frontend/) | HUD gráfico (PySide6/QML) — `python -m frontend` |
| [`integrations/`](integrations/) | Integrações externas (MCP, APIs, serviços de terceiros) — futuro |
| [`tools/`](tools/) | Ferramentas que o JARVIS poderá usar, classificadas por nível de risco — futuro |
| [`config/`](config/) | Configurações do JARVIS: nomes, versão, caminhos, logging (sem segredos versionados) |
| [`tests/`](tests/) | Testes automatizados (`unittest`) do Core e do frontend |
| [`docs/`](docs/) | Documentação técnica e de arquitetura |

Pastas ainda não implementadas contêm um `README.md` explicando sua finalidade futura.

## Funcionalidades

**Implementado (JARVIS v0.8 — HUD Overhaul/UX 2.0, sobre a base do v0.5/v0.6/v0.7):**
- HUD gráfico v3 (PySide6/QML, `python -m frontend`): núcleo de IA com mais profundidade (glow em camadas, anel completo + arcos + segmentado + nós orbitais, ondas de saída em SPEAKING), design system consolidado em `Theme.qml`, layout que cresce em monitores grandes sem estourar em janelas pequenas, boot em etapas (~1,3s), borda de janela sutil, overlay técnico opcional em dev mode
- Voz no HUD: push-to-talk (clique liga/desliga, ou `Ctrl+Space`), reconhecimento de fala offline (Vosk), síntese de fala offline (SAPI5/Windows), estados `LISTENING`/`PROCESSING_SPEECH`/`SPEAKING`, indicador de nível de voz real, botão de microfone (com motivo específico quando indisponível) e controle "OUTPUT" — ver [`frontend/README.md`](frontend/README.md)
- `VoiceService` (`services/voice_service.py`), sob `JarvisApplication`: coordena STT/TTS, cancelamento, eventos `voice.*`
- Chat v2, status v2, overlay de permissão v3 (ênfase visual extra para DANGEROUS), cancelamento, nova conversa
- `JarvisApplication`: API estável para qualquer frontend — `send_message`, `cancel_current_request`, `new_conversation`, `start_listening`/`stop_listening_and_transcribe`/`speak`, `get_status`, `get_messages`, `subscribe`/`events` (ver [`docs/application-api.md`](docs/application-api.md))
- Histórico de conversa em runtime, separado da memória persistente
- Stream de eventos em processo (sem WebSocket/servidor) — consumido tanto pelo HUD quanto preparado para futuros frontends
- Política de concorrência clara (uma requisição por vez, incluindo voz) e cancelamento real via `asyncio.Task`
- Erros estruturados para a interface (`AI_UNAVAILABLE`, `JARVIS_BUSY`, `INTERNAL_ERROR`, `MICROPHONE_UNAVAILABLE`, `STT_NOT_READY`, `TTS_UNAVAILABLE`, `VOICE_CANCELLED`) — nunca texto para analisar
- Fundação de permissões em memória (`app/permissions.py`), não conectada a ferramentas reais ainda — voz nunca contorna essa fundação (ver `docs/architecture.md`)
- Núcleo executável por terminal, assíncrono (`python main.py`), consumindo a mesma Application Layer que o HUD (sem voz — recurso do HUD)
- Comandos: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit`
- Leitura somente-leitura da memória (`profile.md`, `preferences.md`), entregue como contexto controlado à IA
- Arquitetura do **Claude Agent SDK** pronta (`ClaudeAgentProvider`, sessão contínua, ferramentas desabilitadas), condicionada a `ANTHROPIC_API_KEY`; sem a chave, fallback automático e seguro para `UnavailableAIService`

**Preparado, mas não ativado:**
- Conexão real com Claude e sessão de conversa contínua (arquitetura pronta e testada com fakes; não validada com IA real nesta etapa — sem API key neste ambiente)
- Fluxo completo voz → Claude → voz (fala transcrita já cai no chat como texto revisável; a resposta inteligente de verdade depende de uma versão futura ativar o Claude)
- Streaming real token-a-token: contrato de eventos já existe, e o Bridge/`MessageListModel` já sabem reagir a um `response.delta` (testado com eventos fake) — falta só o backend passar a emiti-lo de verdade
- Fluxo de permissões de ferramentas (overlay pronto e visualmente refinado; falta conectar a ferramentas reais)

**Planejado:**
- API Claude real
- Wake word / escuta permanente (por enquanto é só push-to-talk, de propósito)
- Ferramentas para interagir com o computador (READ / ACTION / DANGEROUS)
- MCPs, Skills e Hooks do Claude Code
- Subagentes especializados
- **Ruflo** ([github.com/ruvnet/ruflo](https://github.com/ruvnet/ruflo)) como camada opcional de orquestração multiagente para tarefas complexas — não instalado, não obrigatório para o JARVIS funcionar
- Memória avançada (embeddings, busca semântica)
- Persistência de conversas entre execuções
- Tela de configurações, temas alternativos, empacotamento como aplicativo Windows instalável
- Integração com outras APIs e serviços externos

## Aviso

Este projeto está em desenvolvimento inicial. Boa parte das funcionalidades listadas acima ainda não existe em código — ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento completo de implementado vs. preparado vs. planejado.
