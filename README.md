# JARVIS

Assistente pessoal modular para Windows, com Claude Code como agente/cérebro principal.

## Objetivo

Construir um assistente pessoal com aplicativo próprio para Windows, capaz de conversar por texto e voz, lembrar de contexto sobre o usuário e seus projetos, e usar o Claude Code como motor de raciocínio e execução — com ferramentas, MCPs, skills, hooks e subagentes especializados, sempre sob um sistema de permissões que impede ações perigosas sem confirmação.

## Estado atual

✅ **JARVIS v1.6.0 — Response Pipeline Hardening & Localization.** A cadeia de resposta ficou estanque: raciocínio interno do modelo é separado estruturalmente do conteúdo visível em todos os 6 providers ([`services/providers/http_support.py`](services/providers/http_support.py) cobre `reasoning`, `reasoning_content`, `thinking`, `thought`, `analysis` e afins; o Gemini separa por `thought: true`), e **nunca** é promovido a resposta por falta de conteúdo — uma resposta só com raciocínio é uma resposta vazia, e o router avança na cadeia. Nada disso usa blacklist de texto. O HUD deixou de mostrar identificador técnico (`openai/gpt-oss-20b:free` virou `GPT-OSS 20B`, ver [`services/providers/display_names.py`](services/providers/display_names.py)) enquanto o roteamento continua usando o ID real. O TTS ganhou um sanitizador central ([`services/speech_sanitizer.py`](services/speech_sanitizer.py)) aplicado dentro de `VoiceService.speak`, o ponto único por onde passam fala automática e replay — Markdown e emoji não são mais verbalizados, e acentuação e símbolos como `R$` ficam intactos. Recusas do modelo passaram a ser escopadas à requisição: `RequestContext` isola o estado de cada geração, uma recusa estruturada não entra no histórico reenviado ao provider e `ProviderRefusedError` **encerra** a cadeia em vez de procurar um provider mais permissivo. Por fim, idioma, região e moeda são configuráveis e independentes ([`services/regional_preferences.py`](services/regional_preferences.py)), detectados apenas da configuração regional do sistema — sem GPS, sem IP, sem geolocalização remota — e chegam a todos os providers da cadeia como diretriz de prompt, incluindo a instrução explícita de nunca inventar taxa de câmbio.

✅ **JARVIS v1.5.0 — Account Security & Provider Controls.** A política de senha saiu de "8 caracteres" para um módulo central ([`services/password_policy.py`](services/password_policy.py)) que prioriza comprimento (mínimo 12 em senhas novas), recusa senhas comuns — conferidas **localmente**, nenhuma senha sai desta máquina — e recusa senhas derivadas do próprio username/e-mail/nome. Contas antigas continuam entrando normalmente: a política vale para senhas novas, não invalida hash nenhum. O login ganhou limitação de tentativas por identificador ([`services/login_throttle.py`](services/login_throttle.py)), que fecha a lacuna do backoff por conta: tentativas contra usuários que **não existem** também passam a ser contadas, com backoff progressivo, cooldown temporário e nunca bloqueio permanente. "Active Sessions" agora encerra uma sessão específica (encerrar a atual desloga na hora), e um histórico de atividade de segurança registra login, senha, e-mail, 2FA, código de recuperação e revogação de sessão — **nunca** senha, token, chave de API, segredo TOTP, código em claro ou conteúdo de conversa. O HUD passou a mostrar qual provider/modelo serviu a última resposta e um indicador de fallback que só aparece quando houve fallback de verdade, e uma seção "AI PROVIDERS" lista os 6 providers com um "Test Connection" que faz exatamente uma chamada mínima e devolve status de um vocabulário fechado — sem nunca exibir chave, header ou corpo de resposta.

✅ **JARVIS v1.4.0 — Multi-Provider AI Fallback.** O `ProviderRouter` deixou de falar só com a OpenRouter: agora é uma cadeia de fallback entre 6 providers (OpenRouter → NVIDIA NIM → Gemini → Groq → Cerebras → Mistral), cada um com sua própria lista curada e validada de modelos gratuitos. Um provider indisponível (rate limit, timeout, 5xx, modelo esgotado) faz o router avançar sozinho para o próximo, sem intervenção; um erro de configuração real (credencial inválida, request malformado) **nunca** é mascarado tentando outro provider. `free_only` continua obrigatório em toda a cadeia — nenhuma rota paga é escolhida silenciosamente, mesmo para os providers que não reportam custo por chamada. Detalhes completos, catálogo validado por chamada real e limitações honestas em [`docs/providers.md`](docs/providers.md).

Versões anteriores (chat UX, contas, 2FA, STT com Faster Whisper, etc.) seguem funcionando sem alteração — ver `git log` e os documentos em [`docs/`](docs/) para o histórico completo.



✅ **JARVIS v1.1 — correções de uso real.** Sobre a v1.0, corrige o que apareceu usando o app de verdade: mensagens do chat que apareciam vazias e rotuladas "JARVIS" mesmo sendo do usuário (causa: em Qt 6 um delegate com `required property` deixa de receber o objeto `model`, e os bindings viravam `undefined`); memória que não atravessava conversas (agora há memória de longo prazo por usuário, isolada e deduplicada); `.env` que não era carregado automaticamente; e o status de IA técnico demais no HUD (`OPENROUTER (FREE)` → `AI CONFIGURED`). Sem redesign, sem provider novo, sem billing. Detalhes em [`docs/architecture.md`](docs/architecture.md).

✅ **JARVIS v1.0 — AI integrada, contas endurecidas e verificação de e-mail.** Primeira versão em que o JARVIS **conversa de verdade**: o `ProviderRouter` está conectado ao fluxo real do aplicativo (`JarvisApplication → AIService → ProviderRouter → OpenRouter`), com o JARVIS — não o Ruflo — decidindo provider, modelo e custo. Modo `free_only` ligado por padrão: nunca há queda silenciosa para um modelo pago; sem rota gratuita, o erro é explícito (`NO_FREE_MODEL_AVAILABLE`).

Sobre a base da v0.9, esta versão também endurece o que já existia: token de sessão agora guardado só como hash (com migração que **não** invalidou sessões existentes), proteção contra força bruta no login, verificação real de e-mail (código de 6 dígitos, expira em 5 min, reenvio após 60s, uso único), sistema de migração de banco versionado e transacional, e sanitização do contexto enviado à IA. Sem billing, sem cloud, sem Groq/Gemini/Mistral/NVIDIA reais — ver [`docs/security.md`](docs/security.md) para o modelo de segurança completo (e suas limitações honestas), [`docs/providers.md`](docs/providers.md) para o Provider Router e [`docs/architecture.md`](docs/architecture.md) para a arquitetura.

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

### Setup em um comando

```powershell
python setup.py
```

Instala as dependências (`requirements.txt`), instala o JARVIS em modo editável (registrando o comando `jarvis`), baixa o modelo de reconhecimento de fala se ainda não existir, e verifica o microfone. Ao final:

```
JARVIS SETUP COMPLETE

Launcher .......... OK
Dependencies ...... OK
Speech-to-Text .... READY
Microphone ........ DETECTED

Open a new terminal and run:

    jarvis
```

**Idempotente**: rodar de novo não rebaixa o modelo se ele já estiver íntegro. A validação é mais rigorosa que "a pasta existe" — uma extração interrompida é detectada e refeita.

**Sem microfone o setup não falha**: apenas reporta `NOT DETECTED`. O JARVIS funciona normalmente por texto.

O download usa a fonte oficial já definida pelo projeto (`services/vosk_model_manager.py`: alphacephei.com, mantenedores do Vosk — HTTPS obrigatório, arquivo temporário, progresso real, proteção contra Zip Slip, limpeza em caso de falha). O modelo vai para `data/models/vosk/`, fora do Git.

> `setup.py` tem dois modos, de propósito: executado direto (`python setup.py`) faz a preparação acima; executado pelo pip com um comando de build (`pip install -e .`) delega ao setuptools. Sem essa distinção, um `pip install` dispararia o download de 45 MB.

### Starting JARVIS

Depois de uma instalação editável (abaixo), o HUD abre com um comando curto:

```powershell
jarvis
```

Aliases equivalentes — todos abrem exatamente a mesma GUI:

```powershell
jarvis wake up
jarvis wake
jarvis start
```

`jarvis --help` mostra o uso; um argumento desconhecido mostra o mesmo texto e sai com código 2.

**Zerar todas as contas locais:**

```powershell
jarvis delete all users
```

Pede confirmação — só prossegue se você digitar exatamente `DELETE`; qualquer outra resposta cancela sem apagar nada. Antes de apagar, salva um backup do banco em `data/backups/jarvis-before-delete-users-<data>.db` (se o backup falhar, a operação aborta e nada é removido).

Remove contas, sessões, conversas, mensagens, memórias e tokens de verificação, além das pastas de memória por usuário em `data/users/`. **Preserva** `.env`, chaves de API, configuração de SMTP, modelos de voz e o resto do projeto. O banco não é apagado nem recriado — continua válido e migrado, e o app abre como instalação nova.

**Instalação editável** (registra o comando; roda uma vez):

```powershell
pip install -e .
```

O `pyproject.toml` declara **apenas** o entrypoint — as dependências continuam em `requirements.txt` (`pip install -r requirements.txt`), então `pip install -e .` é rápido e não reinstala nada. Editável significa que o comando sempre executa o código atual do repositório, sem reinstalar a cada alteração.

`jarvis` é um atalho para o mesmo entrypoint de `python -m frontend` (`frontend/launcher.py::run`) — mesmo `.env`, mesmo auto-login, mesmo ProviderRouter, mesma voz, mesmo encerramento. **`python -m frontend` continua funcionando normalmente.**

**Se o comando não for encontrado (Windows):** o `pip` instala o executável em uma pasta `Scripts` que pode não estar no `PATH` — ele avisa isso na instalação, por exemplo `C:\Users\<você>\AppData\Roaming\Python\Python314\Scripts`. Três opções, em ordem de preferência:

1. **Use um ambiente virtual** (recomendado): com o venv ativo, o `Scripts` dele já está no `PATH`, e `jarvis` funciona direto.
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   pip install -e .
   jarvis
   ```
2. **Sem venv**, chame pelo caminho completo, ou adicione aquela pasta `Scripts` ao `PATH` do usuário pelas Configurações do Windows (Variáveis de Ambiente). Este projeto **não** altera seu `PATH` automaticamente.
3. **Sempre funciona, sem instalar nada:** `python -m frontend`.

Sem nenhuma chave de IA configurada, o JARVIS (em qualquer um dos dois) abre e funciona normalmente, avisando que a IA não está configurada — nunca finge uma resposta, nunca trava.

**Para conversar de verdade (recomendado — OpenRouter, rota gratuita):**

```powershell
$env:OPENROUTER_API_KEY = "sua-chave-aqui"
python -m frontend        # ou: python main.py
```

Com isso o JARVIS usa o `ProviderRouter` com `free_only` ligado por padrão: só rotas gratuitas, e **nunca** um fallback silencioso para modelo pago (ver [`docs/providers.md`](docs/providers.md)). `ANTHROPIC_API_KEY` continua funcionando como alternativa (Claude Agent SDK) — OpenRouter tem precedência quando as duas existem. Nenhuma chave vai para código, banco ou Git: sempre lidas do ambiente (veja [`.env.example`](.env.example)).

Comandos disponíveis no terminal: `/help`, `/status`, `/memory`, `/new` (alias `/reset`), `/clear`, `/exit` (alias `/quit`). O terminal continua sem contas (fala direto com `JarvisApplication`, memória global em `memory/`) — contas, sidebar e chats persistidos são só do HUD.

**Contas (só no HUD):** ao abrir o HUD pela primeira vez, é preciso criar uma conta local (usuário, nome, e-mail e senha) ou entrar em uma já existente. A sessão fica salva localmente (token cifrado via Windows DPAPI, nunca a senha) para continuar logado nas próximas execuções, até um logout explícito. Cada conta tem seus próprios chats e sua própria memória.

**Verificação de e-mail (v1.0):** contas novas recebem um código de 6 dígitos por e-mail (expira em 5 min, reenvio após 60s). Isso exige SMTP configurado (`JARVIS_SMTP_HOST`, `JARVIS_EMAIL_FROM`, ...); **sem SMTP o JARVIS não finge ter enviado** — diz claramente que o envio não está configurado, e a conta continua utilizável. Contas criadas na v0.9 (sem e-mail) continuam funcionando e podem adicionar um e-mail depois pelo painel de conta. Detalhes em [`docs/security.md`](docs/security.md).

**Voz (opcional, só no HUD):** `requirements.txt` já inclui as dependências de voz (`vosk`, `sounddevice`, `pyttsx3` — nenhuma exige GPU). A síntese de fala (TTS) funciona assim que essas dependências estiverem instaladas, usando vozes já existentes no Windows. O reconhecimento de fala (STT) exige, além disso, um modelo Vosk instalado — agora **instalável de dentro do próprio HUD**: clique no microfone sem o modelo instalado abre um passo explícito "Baixar modelo de voz (~45 MB)", mostrando origem/licença/tamanho antes de qualquer download (nunca automático). Sem o modelo, o botão de microfone mostra claramente "configuração necessária" (nunca some nem finge estar pronto) e o resto do JARVIS funciona normalmente por texto — ver [`frontend/README.md`](frontend/README.md#voz-v09--setup-do-modelo-e-push-to-talk-corrigido).

## Arquitetura geral

```
JARVIS HUD (PySide6/QML)                          Terminal
  ↓                                                  ↓
AccountManager   (app/account_manager.py — só no HUD: contas, sessão, chats, memória por usuário, verificação de e-mail)
  ↓ (dono do ciclo de vida por sessão logada)
JarvisApplication          (app/application.py — a API estável para qualquer frontend)
  ├→ VoiceService           (services/voice_service.py — STT/TTS, só usado pelo HUD)
  ↓
JarvisCore / Orchestrator   (app/core.py, app/orchestrator.py)
  ↓
AIService                   (services/ai_service.py — abstração)
  ├→ ProviderRouterAIService → ProviderRouter → OpenRouter   (v1.0, com OPENROUTER_API_KEY)
  ├→ ClaudeAgentProvider     → Claude Agent SDK              (com ANTHROPIC_API_KEY)
  └→ UnavailableAIService                                     (sem nenhuma chave — app continua funcionando)
  ↓
Ferramentas / Skills / MCP / Subagentes   (planejado)
  ↓
Sistema operacional / APIs / serviços      (planejado)
```

O terminal continua falando direto com `JarvisApplication` (sem `AccountManager`, sem contas) — só o HUD tem a camada de contas.

A cadeia de IA na v1.0:

```
JarvisApplication → Orchestrator → AIService → ProviderRouter → OpenRouter → modelo
```

`ProviderRouter` é a **única autoridade** sobre provider/modelo/custo. O Ruflo (opcional, ferramenta de desenvolvimento) coordena agentes e **nunca** decide modelo — ver [`docs/ruflo-integration.md`](docs/ruflo-integration.md) para o bug de model routing que motivou essa separação.

Detalhes completos, incluindo o que está implementado vs. preparado vs. planejado, em [`docs/architecture.md`](docs/architecture.md). API pública da Application Layer em [`docs/application-api.md`](docs/application-api.md); Provider Router em [`docs/providers.md`](docs/providers.md); segurança em [`docs/security.md`](docs/security.md); arquitetura do HUD em [`frontend/README.md`](frontend/README.md).

## Estrutura de pastas

| Pasta | Responsabilidade |
|---|---|
| [`.claude/`](.claude/) | Configuração do Claude Code: agentes e skills do projeto |
| [`memory/`](memory/) | Memória persistente sobre o usuário (perfil, preferências) |
| [`projects/`](projects/) | Contexto persistente de projetos acompanhados pelo JARVIS |
| [`daily/`](daily/) | Registros diários (`YYYY-MM-DD.md`) |
| [`app/`](app/) | Aplicativo/núcleo principal — `AccountManager` (contas/sessão/verificação, só HUD), `JarvisApplication` (fronteira estável), terminal, `JarvisCore`, `Orchestrator`, comandos, estado (async) |
| [`services/`](services/) | Serviços internos — memória (leitura + sanitização de contexto), IA (`ProviderRouterAIService`/`ClaudeAgentProvider`/`UnavailableAIService`), `providers/` (Provider Router), contas/sessão/conversas/verificação de e-mail (SQLite), envio de e-mail, modelo de voz, identidade de runtime, event bus |
| [`data/`](data/) | Dados locais pessoais: banco de contas/chats, sessão local, modelo de voz baixado — **nunca no Git** (ver `.gitignore`) |
| [`frontend/`](frontend/) | HUD gráfico (PySide6/QML) — `python -m frontend` |
| [`integrations/`](integrations/) | Integrações externas (MCP, APIs, serviços de terceiros) — futuro |
| [`tools/`](tools/) | Ferramentas que o JARVIS poderá usar, classificadas por nível de risco — futuro |
| [`config/`](config/) | Configurações do JARVIS: nomes, versão, caminhos, logging (sem segredos versionados) |
| [`tests/`](tests/) | Testes automatizados (`unittest`) do Core e do frontend |
| [`docs/`](docs/) | Documentação técnica e de arquitetura |

Pastas ainda não implementadas contêm um `README.md` explicando sua finalidade futura.

## Funcionalidades

**Implementado (JARVIS v1.0 — AI integrada + hardening, sobre a base do v0.5–v0.9):**
- **IA real conectada ao fluxo do app**: `JarvisApplication → Orchestrator → AIService → ProviderRouter → OpenRouter → modelo`. O `ProviderRouterAIService` (`services/provider_ai_service.py`) adapta a interface `AIService` já existente ao router, mantendo a sessão de conversa em RAM e reenviando o histórico (a API da OpenRouter é stateless, ao contrário do Agent SDK) — nada acima do adaptador precisou mudar
- **`free_only` ligado por padrão**: verificação em duas camadas (nunca solicita modelo fora de `free_models()`; e confere `served_model`/`cost` da resposta antes de aceitá-la). Sem rota gratuita ⇒ `NO_FREE_MODEL_AVAILABLE`, nunca queda silenciosa para pago
- **Verificação de e-mail**: código de 6 dígitos, expira em 5 min, reenvio após 60s, uso único, novo invalida o anterior, máximo de 5 tentativas — tudo validado no backend contra timestamps persistidos (o QML só decrementa a exibição). Sem SMTP configurado, o JARVIS **não finge** ter enviado
- **Hardening de auth**: token de sessão guardado só como SHA-256 no banco (migração converteu os existentes **sem invalidar sessões**), backoff progressivo contra força bruta (nunca bloqueio permanente), defesa contra enumeração de contas por mensagem e por timing
- **Migração de banco versionada e transacional** (`PRAGMA user_version`): falha ⇒ rollback + erro claro, banco preservado. Nunca recriamos o banco para "resolver" divergência de schema
- **Sanitização do contexto enviado à IA** (`services/context_builder.py`): data minimization (teto de caracteres) + redação de segredos que tenham vazado para dentro da memória — aplicada num ponto único, então todo provider recebe memória já tratada
- Contas locais no HUD: criar conta (usuário/nome/e-mail/senha, hash `scrypt`, salt via `secrets`, comparação em tempo constante), entrar, sair; sessão persistida entre execuções; isolamento garantido no nível de query — um usuário nunca lê/escreve dado de outro (ver [`docs/security.md`](docs/security.md))
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
- Arquitetura do **Claude Agent SDK** preservada (`ClaudeAgentProvider`), condicionada a `ANTHROPIC_API_KEY`; sem nenhuma chave, fallback automático e seguro para `UnavailableAIService` — o app abre e o chat não quebra
- 300 testes automatizados, todos offline (nenhum gasta requisição, envia e-mail, usa microfone real ou chama Anthropic)

**Preparado, mas não ativado:**
- Streaming real token-a-token: o contrato de eventos existe (`response.delta`) e o HUD já sabe reagir (testado com eventos fake), mas a v1.0 entrega resposta completa — streaming honesto sobre a API da OpenRouter é a próxima evolução (ver [`docs/providers.md`](docs/providers.md))
- Groq, Gemini, Mistral, NVIDIA: presentes no registry como `NOT_IMPLEMENTED`, sem classe real
- Sincronização de contas/chats na nuvem (arquitetura local-first não impede; nada disso existe agora)
- Cobrança real do plano PRO (estrutura de entitlements pronta; sem Stripe/Pix/checkout)
- Fluxo completo voz → IA → voz: a transcrição cai no campo de texto para revisão, e a resposta pode ser falada via TTS — o que falta é só o usuário não precisar apertar enviar

**Planejado:**
- Streaming token-a-token de verdade
- Providers adicionais reais (Groq, Gemini, Mistral, NVIDIA, Anthropic pelo mesmo router)
- Backend/cloud, auth remota e billing real
- Recuperação de senha / revogação remota de sessão
- Wake word / escuta permanente (por enquanto é só push-to-talk, de propósito)
- Ferramentas para interagir com o computador (READ / ACTION / DANGEROUS), MCPs, Skills e Hooks do Claude Code, subagentes especializados
- **Ruflo** como camada opcional de orquestração multiagente — hoje é só ferramenta de desenvolvimento, e nunca decide provider/modelo (ver [`docs/ruflo-integration.md`](docs/ruflo-integration.md))
- Memória avançada (embeddings, busca semântica)
- Tela de configurações, temas alternativos, empacotamento como aplicativo Windows instalável

## Aviso

O JARVIS v1.0 é uma base estável e funcional, mas continua em evolução: várias capacidades listadas como *planejadas* acima ainda não existem em código. Ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento de implementado vs. preparado vs. planejado, e [`docs/security.md`](docs/security.md) para as limitações de segurança conhecidas (incluindo o fato de o plano FREE/PRO ser local e alterável por quem controla a máquina).
