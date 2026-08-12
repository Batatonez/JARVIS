# JARVIS

Assistente pessoal modular para Windows, com Claude Code como agente/cérebro principal.

## Objetivo

Construir um assistente pessoal com aplicativo próprio para Windows, capaz de conversar por texto e voz, lembrar de contexto sobre o usuário e seus projetos, e usar o Claude Code como motor de raciocínio e execução — com ferramentas, MCPs, skills, hooks e subagentes especializados, sempre sob um sistema de permissões que impede ações perigosas sem confirmação.

## Estado atual

🚧 **JARVIS v0.9 — Accounts, Persistent Chats & Voice Input Fix.** Contas locais (usuário/senha, hash `scrypt`, sessão persistida entre execuções), sidebar retrátil com chats persistidos em SQLite (busca, agrupamento por data, renomear/excluir), memória isolada por conta (com migração controlada da memória legacy pré-contas), fundação FREE/PRO sem cobrança real, e a correção de verdade do microfone: o botão de voz agora distingue corretamente "modelo de reconhecimento ainda não instalado" de "sem microfone" de "erro real", com um fluxo de instalação explícito do modelo Vosk (download só sob consentimento, nunca automático) e captura adaptada ao sample rate real do dispositivo (não mais 16 kHz fixo). HUD v0.8 preservado (mesmo design system, mesmo núcleo de IA). Ainda sem Claude real, Ruflo, MCP/tools ou billing — ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento completo e [`frontend/README.md`](frontend/README.md) para contas/sidebar/voz no HUD.

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

Comandos disponíveis no terminal: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit` (alias `/quit`). O terminal continua sem contas (fala direto com `JarvisApplication`, memória global em `memory/`) — contas, sidebar e chats persistidos são só do HUD nesta versão. No HUD, os mesmos conceitos existem como controles visuais (ver [`frontend/README.md`](frontend/README.md)).

**Contas (só no HUD):** ao abrir o HUD pela primeira vez, é preciso criar uma conta local (usuário/senha — nunca e-mail, nunca dado desnecessário) ou entrar em uma já existente. A sessão fica salva localmente (cifrada via Windows DPAPI quando disponível) para continuar logado nas próximas execuções, até um logout explícito. Cada conta tem seus próprios chats e sua própria memória — ver [`docs/architecture.md`](docs/architecture.md), seção Contas.

**Voz (opcional, só no HUD):** `requirements.txt` já inclui as dependências de voz (`vosk`, `sounddevice`, `pyttsx3` — nenhuma exige GPU). A síntese de fala (TTS) funciona assim que essas dependências estiverem instaladas, usando vozes já existentes no Windows. O reconhecimento de fala (STT) exige, além disso, um modelo Vosk instalado — agora **instalável de dentro do próprio HUD**: clique no microfone sem o modelo instalado abre um passo explícito "Baixar modelo de voz (~45 MB)", mostrando origem/licença/tamanho antes de qualquer download (nunca automático). Sem o modelo, o botão de microfone mostra claramente "configuração necessária" (nunca some nem finge estar pronto) e o resto do JARVIS funciona normalmente por texto — ver [`frontend/README.md`](frontend/README.md#voz-v09--setup-do-modelo-e-push-to-talk-corrigido).

## Arquitetura geral

```
JARVIS HUD (PySide6/QML)                          Terminal
  ↓                                                  ↓
AccountManager   (implementado — v0.9 — app/account_manager.py — só no HUD: contas, sessão, chats, memória por usuário)
  ↓ (dono do ciclo de vida por sessão logada)
JarvisApplication          (implementado — app/application.py — a API estável para qualquer frontend)
  ├→ VoiceService           (implementado — v0.7/v0.9 — services/voice_service.py — STT/TTS, só usado pelo HUD)
  ↓
JarvisCore / Orchestrator   (implementado — app/core.py, app/orchestrator.py)
  ↓
Claude Code                 (arquitetura pronta via ClaudeAgentProvider/Claude Agent SDK — services/claude_agent_provider.py)
  ↓
Ferramentas / Skills / MCP / Subagentes
  ↓
Sistema operacional / APIs / serviços
```

O terminal continua falando direto com `JarvisApplication` (sem `AccountManager`, sem contas) — só o HUD ganhou a camada de contas nesta versão.

Detalhes completos, incluindo o que está implementado vs. preparado vs. planejado (e a direção futura com Ruflo para orquestração multiagente), em [`docs/architecture.md`](docs/architecture.md). API pública da Application Layer em [`docs/application-api.md`](docs/application-api.md); arquitetura do HUD em [`frontend/README.md`](frontend/README.md).

## Estrutura de pastas

| Pasta | Responsabilidade |
|---|---|
| [`.claude/`](.claude/) | Configuração do Claude Code: agentes e skills do projeto |
| [`memory/`](memory/) | Memória persistente sobre o usuário (perfil, preferências) |
| [`projects/`](projects/) | Contexto persistente de projetos acompanhados pelo JARVIS |
| [`daily/`](daily/) | Registros diários (`YYYY-MM-DD.md`) |
| [`app/`](app/) | Aplicativo/núcleo principal — `AccountManager` (contas/sessão, v0.9, só HUD), `JarvisApplication` (fronteira estável), terminal, `JarvisCore`, `Orchestrator`, comandos, estado (async) |
| [`services/`](services/) | Serviços internos — memória (leitura), IA (`ClaudeAgentProvider`/`UnavailableAIService`), contas/sessão/conversas (SQLite), modelo de voz (`VoiceModelManager`), identidade de runtime, event bus |
| [`data/`](data/) | Dados locais pessoais (v0.9): banco de contas/chats, sessão local, modelo de voz baixado — **nunca no Git** (ver `.gitignore`) |
| [`frontend/`](frontend/) | HUD gráfico (PySide6/QML) — `python -m frontend` |
| [`integrations/`](integrations/) | Integrações externas (MCP, APIs, serviços de terceiros) — futuro |
| [`tools/`](tools/) | Ferramentas que o JARVIS poderá usar, classificadas por nível de risco — futuro |
| [`config/`](config/) | Configurações do JARVIS: nomes, versão, caminhos, logging (sem segredos versionados) |
| [`tests/`](tests/) | Testes automatizados (`unittest`) do Core e do frontend |
| [`docs/`](docs/) | Documentação técnica e de arquitetura |

Pastas ainda não implementadas contêm um `README.md` explicando sua finalidade futura.

## Funcionalidades

**Implementado (JARVIS v0.9 — Accounts, Persistent Chats & Voice Input Fix, sobre a base do v0.5–v0.8):**
- Contas locais no HUD: criar conta (usuário/senha, hash `scrypt`, salt via `secrets`, comparação em tempo constante), entrar, sair; sessão persistida entre execuções (token opaco, nunca a senha, cifrado via Windows DPAPI quando disponível); isolamento garantido no nível de query — um usuário nunca lê/escreve dado de outro (ver `services/user_repository.py`, `services/session_repository.py`, `services/session_store.py`)
- Sidebar retrátil (expandida/colapsada, animada) com "+ Novo chat", busca local, conversas agrupadas por data (Hoje/Ontem/Últimos 7 dias/Mais antigos), conta/plano no rodapé — substitui o antigo botão solto "NOVA CONVERSA"
- Chats persistidos em SQLite (`services/local_database.py`, `services/conversation_repository.py`, stdlib `sqlite3`, sem ORM): criar, listar, ordenar, buscar, renomear, excluir, carregar conversa antiga — sempre escopado ao usuário logado; título derivado das primeiras palavras da primeira mensagem (sem IA)
- Memória isolada por conta (`data/users/<id>/memory/`), com migração controlada da memória legacy pré-contas (`memory/profile.md`/`preferences.md`) para a primeira conta criada no ambiente — original nunca apagado/movido, nunca sobrescreve memória que a conta já tenha
- Fundação FREE/PRO (`app/entitlements.py`): um único ponto (`entitlements_for(plan)`) resolve capacidades por plano — sem cobrança, sem checkout, sem Stripe/Pix real
- **Microfone corrigido de verdade**: `services/stt_service.py` agora distingue `READY`/`SETUP_REQUIRED`/`NO_MICROPHONE`/`UNAVAILABLE` (antes caía tudo em "indisponível" sem dizer por quê); `VoiceModelManager` (`services/vosk_model_manager.py`) instala o modelo Vosk só sob consentimento explícito no HUD (nunca automático), com download HTTPS, progresso real, cancelamento, e proteção contra Zip Slip; `services/vosk_stt_provider.py` captura no sample rate nativo do dispositivo (detectado, nunca mais 16 kHz fixo) e reamostra em software — ver seção "Diagnóstico" em [`docs/architecture.md`](docs/architecture.md)
- MicButton com 5 estados visuais (`SETUP_REQUIRED`/`READY`/`LISTENING`/`PROCESSING`/`ERROR`), cada um com tooltip própria e comportamento de clique correto (nunca dispara uma segunda captura durante `PROCESSING`)
- HUD gráfico v3 preservado integralmente (PySide6/QML, `python -m frontend`): núcleo de IA, design system em `Theme.qml`, layout responsivo, boot em etapas, overlay técnico em dev mode
- Voz no HUD: push-to-talk (clique liga/desliga, ou `Ctrl+Space`), síntese de fala offline (SAPI5/Windows), estados `LISTENING`/`PROCESSING_SPEECH`/`SPEAKING`, indicador de nível de voz real — ver [`frontend/README.md`](frontend/README.md)
- `VoiceService`/`JarvisApplication`/`Orchestrator`/permissões/cancelamento/stream de eventos: tudo do v0.5–v0.8 preservado sem regressão (ver [`docs/application-api.md`](docs/application-api.md))
- Núcleo executável por terminal, assíncrono (`python main.py`), sem contas (fala direto com `JarvisApplication`, memória global)
- Comandos: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit`
- Arquitetura do **Claude Agent SDK** pronta (`ClaudeAgentProvider`), condicionada a `ANTHROPIC_API_KEY`; sem a chave, fallback automático e seguro para `UnavailableAIService`

**Preparado, mas não ativado:**
- Conexão real com Claude e sessão de conversa contínua (arquitetura pronta e testada com fakes; não validada com IA real nesta etapa — sem API key neste ambiente)
- Fluxo completo voz → Claude → voz (fala transcrita já cai no chat como texto revisável; a resposta inteligente de verdade depende de uma versão futura ativar o Claude)
- Streaming real token-a-token: contrato de eventos já existe (`response.delta`), testado com eventos fake — falta só o backend emitir de verdade
- Sincronização de contas/chats na nuvem (arquitetura local-first não impede isso no futuro; nada disso existe agora)
- Cobrança real do plano PRO (estrutura de entitlements pronta; sem Stripe/Pix/checkout)

**Planejado:**
- API Claude real
- Verificação de e-mail / recuperação de conta
- Backend/cloud e billing real
- Wake word / escuta permanente (por enquanto é só push-to-talk, de propósito)
- Ferramentas para interagir com o computador (READ / ACTION / DANGEROUS), MCPs, Skills e Hooks do Claude Code, subagentes especializados
- **Ruflo** ([github.com/ruvnet/ruflo](https://github.com/ruvnet/ruflo)) como camada opcional de orquestração multiagente para tarefas complexas — não instalado, não obrigatório para o JARVIS funcionar
- Memória avançada (embeddings, busca semântica)
- Tela de configurações, temas alternativos, empacotamento como aplicativo Windows instalável
- Integração com outras APIs e serviços externos

## Aviso

Este projeto está em desenvolvimento inicial. Boa parte das funcionalidades listadas acima ainda não existe em código — ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento completo de implementado vs. preparado vs. planejado.
