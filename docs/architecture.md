# Arquitetura do JARVIS

> **Aviso:** este documento mistura o que já existe (**IMPLEMENTADO**) com o que está **PREPARADO, MAS NÃO ATIVADO** e o que é puro **PLANEJADO**. Cada seção diz explicitamente qual é o caso. Não presuma que algo existe só porque está no diagrama — o diagrama descreve a arquitetura-alvo completa, não o estado atual.

## Estado atual em uma frase

**JARVIS v1.1 — correções de uso real**: sobre a v1.0, corrige o que apareceu usando o app de verdade — o bug das mensagens vazias/rotuladas erradas no chat, memória que não atravessava conversas, `.env` que não era carregado sozinho, e o status de IA técnico demais na tela. Nada de redesign, nenhum provider novo. Ver "Memória de longo prazo" e "Configuração via `.env`" abaixo.

**JARVIS v1.0 — AI integrada e hardening**: a cadeia de IA passou a ser real — `JarvisApplication → Orchestrator → AIService → ProviderRouter → OpenRouter → modelo` — com o JARVIS (não o Ruflo) decidindo provider/modelo/custo e `free_only` ligado por padrão. Sobre isso, a v1.0 endurece o que a v0.9 criou: token de sessão só como hash no banco (migrado sem invalidar sessões), backoff contra força bruta, verificação real de e-mail, migração de schema versionada e transacional, e sanitização do contexto que sai da máquina. O terminal continua sem contas. Detalhes de UI ficam em [`frontend/README.md`](../frontend/README.md); segurança em [`docs/security.md`](security.md); Provider Router em [`docs/providers.md`](providers.md).

## Visão geral (arquitetura-alvo, planejada)

```
                    ┌─────────────────────┐
                    │      FRONTENDS      │
                    │  HUD (voz incluída) │
                    │   / CLI (terminal)  │
                    └──────────┬──────────┘
                               │
                               ↓
                    ┌─────────────────────┐
                    │ JARVIS APPLICATION  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼──────────────────┐
              ↓                ↓                   ↓
           Core           Event Stream        VoiceService (v0.7)
              │                                     │
        Orchestrator                          ┌─────┴─────┐
              │                                ↓           ↓
        ┌─────┼──────┐                       STT         TTS
        ↓     ↓      ↓                     (Vosk)      (SAPI5)
   AIService Memory Services
        ↓
ClaudeAgentProvider


FUTURO (planejado, não implementado):

Orchestrator
    ↓
AgentOrchestration
    ↓
Ruflo
```

Em paralelo:

```
Claude Code  ↔  Memory
Hooks        →  App / HUD
Voice STT    →  Application     (implementado, v0.7 — texto cai no input, não vai à IA sozinho)
Application  →  TTS             (implementado, v0.7 — fala respostas quando voice_output_enabled)
```

## Application Layer — a fronteira entre Core e frontend

**Status: implementado (v0.4).** `JarvisApplication` (`app/application.py`) é a **única** porta de entrada que um frontend deve usar — terminal e HUD hoje, voz no futuro. Documentação completa da API pública em [`docs/application-api.md`](application-api.md); aqui vai só o desenho:

```
Frontend (terminal, futuro HUD, futura voz)
    ↓
JarvisApplication
    ├── send_message() / cancel_current_request() / new_conversation()
    ├── get_status() / get_messages() / memory_status()
    ├── subscribe() / unsubscribe() / events()
    └── permissions  (PermissionService — fundação, não conectada a ferramentas)
    ↓
JarvisCore  →  Orchestrator  →  AIService  →  ClaudeAgentProvider
```

Um frontend **nunca** deve fazer `from services.claude_agent_provider import ClaudeAgentProvider`, `memory_service.get_profile()` ou `orchestrator.handle(...)` diretamente — só `JarvisApplication`. Modelos de domínio (`app/models.py`: `Message`, `AssistantResponse`, `StatusSnapshot`, `AppEvent`, `PermissionRequest`, etc.) usam só a biblioteca padrão e nunca carregam um tipo do Claude Agent SDK — testado explicitamente (`tests/test_application.py::NoSdkLeakageTests`).

**Conversation vs. Memory** — dois conceitos deliberadamente separados:
- `app/conversation.py` (`Conversation`) — histórico da sessão de chat atual, só em RAM, com limite configurável (`JARVIS_MAX_CONVERSATION_MESSAGES`, padrão 200). Desaparece ao encerrar o JARVIS. Nunca é escrito em `memory/` ou `daily/`.
- `memory/` — memória persistente sobre o usuário (perfil, preferências). Somente leitura em runtime, inalterada por conversas.

**Concorrência** — política escolhida: **uma requisição ativa por conversa**, rejeição limpa (não fila). `send_message()` verifica `self._current_request_task`; se ocupado, devolve `AssistantResponse(status=ERROR, error.code=JARVIS_BUSY)` na hora, sem enfileirar. Mais simples e previsível para uma GUI (estado "ocupado" explícito) do que uma fila implícita — ver `tests/test_application.py::BusyAndConcurrencyTests`.

**Cancelamento** — `cancel_current_request()` usa `asyncio.Task.cancel()` (nunca mata processo). O `finally` já existente em `Orchestrator._handle_message` garante que o estado volta para `IDLE` mesmo quando a tarefa é cancelada no meio do caminho — testado em `tests/test_application.py::CancellationTests`.

**Erros para a interface** — `send_message()` nunca propaga uma exceção crua para quem chama: falhas esperadas (`AI_UNAVAILABLE`, `JARVIS_BUSY`) e inesperadas (`INTERNAL_ERROR`) sempre viram `AssistantResponse` com `status`/`error.code` estruturados. O frontend distingue sucesso/cancelado/erro sem nunca precisar analisar texto humano.

**Eventos** — `JarvisApplication` relaia uma parte dos eventos do `EventBus` interno (`jarvis.started/stopped`, `state.changed`, `ai.connected/disconnected`) e emite diretamente os seus próprios, mais ricos que os internos (`message.received`, `response.started/completed/failed`, `conversation.started/cleared`, `jarvis.stopping`, `permission.requested/resolved`) — nenhum evento é emitido duas vezes pelo mesmo motivo em camadas diferentes.

**Streaming (preparado, não exposto)** — `send_message()` devolve o texto final via `AssistantResponse.content` (`str`), mas o contrato de eventos já inclui `response.started` → `response.completed`/`response.failed`; adicionar `response.delta` para token-a-token é uma extensão local a `send_message()`/`ClaudeAgentProvider.ask()`, sem mudar a API pública.

## Como isso mapeia para o código hoje (v0.9)

Existem dois frontends reais, os dois indiretamente sobre o mesmo `JarvisApplication` — nenhum fala com `JarvisCore` diretamente. Só o HUD passa por `AccountManager` (contas); o terminal fala com `JarvisApplication` direto, sem login. Todo o caminho é assíncrono (`asyncio`), cada frontend com seu próprio event loop (`main.py` usa `asyncio.run(...)`; `frontend/launcher.py` usa `PySide6.QtAsyncio.run(...)`, ver seção seguinte):

```
main.py  →  asyncio.run(...)              frontend/__main__.py  →  QtAsyncio.run(...)
  ↓                                          ↓
app/terminal.py (async)                    frontend/launcher.py + frontend/bridge.py (JarvisBridge)
  ↓                                          ↓
  │                                        app/account_manager.py (AccountManager: contas, sessão — só HUD)
  │                                          ↓
                    app/application.py     (JarvisApplication: fronteira estável — async)
                              ↓
                    app/core.py             (JarvisCore: fachada, estado, ciclo de vida — async)
                              ↓
                    app/orchestrator.py     (Orchestrator: comando vs. mensagem — async)
                              ↓
                    app/commands.py         (comandos internos, síncronos: /help /status /memory /new /clear /exit)
                              ↓
                    services/                (memory_service, ai_service + create_ai_service, claude_agent_provider, runtime_identity, event_bus, user_repository, session_repository, conversation_repository)
```

## Frontend/HUD — [`frontend/`](../frontend/)

**Status: implementado (v0.5, refinado visualmente no v0.6/v0.8; auth-first desde o v0.9).** Segundo frontend, ao lado do terminal — não substitui `app/terminal.py`, os dois continuam existindo, mas só o HUD fala com `AccountManager`/contas. Documentação completa em [`frontend/README.md`](../frontend/README.md); aqui vai só o desenho arquitetural:

```
JARVIS HUD (PySide6 / QML)
    ↓
frontend/bridge.py (JarvisBridge — QObject, sem lógica de domínio, só tradução)
    ↓
app/account_manager.py (AccountManager — contas/sessão, dono do ciclo de vida por login)
    ↓
JarvisApplication   (só existe enquanto alguém está logado)
```

O QML **nunca** importa `app/core.py`, `app/application.py`, `app/account_manager.py`, `services/` ou o Claude Agent SDK — só conhece `bridge`, exposto como propriedade de contexto (`engine.rootContext().setContextProperty("bridge", bridge)`). `JarvisBridge` expõe, além das Properties/Slots de chat/voz/permissão de sempre (`jarvisState`, `running`, `busy`, `messages`, `devMode`, `canClose`, `sendMessage`, `cancelCurrentRequest`, `approvePermission`, `denyPermission`, `requestShutdown`), as Properties/Slots de conta (v0.9): `authenticated`, `currentUser`, `conversations`, `currentConversationId`, `sttStatus`, `voiceModelInstalled`/`voiceModelDownloadActive`/`voiceModelDownloadProgress`, e os Slots `register`/`login`/`logout`, `startNewConversation`/`openConversation`/`searchConversations`/`renameConversation`/`deleteConversation`, `downloadVoiceModel`/`cancelVoiceModelDownload` — QML nunca interpreta string de `/status`, e nunca vê SQLite.

**`self._app` é uma property, não uma referência fixa (v0.9)**: como `JarvisCore`/`JarvisApplication` só existem enquanto há uma sessão logada (`AccountManager` os cria no login e os destrói no logout/shutdown), `JarvisBridge._app` sempre lê `self._account.app` na hora — nunca guarda a instância. Todo slot que precisa da Application Layer checa `if self._app is not None` antes de agir; antes do login, `_app` é `None` e o QML mostra a tela de autenticação (`AuthScreen`) em vez do HUD.

**Orientado a eventos, nunca a polling**: `JarvisBridge.initialize()` (chamado uma vez, no início do processo) tenta um auto-login a partir da sessão local persistida (`AccountManager.try_auto_login()`); se bem-sucedido, ou a cada novo login, `_enter_session()` chama `JarvisApplication.subscribe()` (síncrono — a fila é registrada imediatamente) e consome essa fila em uma única task de fundo por sessão; cada evento relevante dispara uma releitura pontual de `get_status()`/`get_messages()`/lista de conversas, nunca em loop/timer.

**Integração Qt + asyncio**: `PySide6.QtAsyncio` (módulo oficial do Qt for Python, incluso no `PySide6` já instalado — nenhuma dependência extra como `qasync`) funde o event loop do Qt e do asyncio em um só processo, sem thread extra e sem recriar o loop por mensagem.

Quando o HUD é fechado, `Window.onClosing` intercepta e chama `bridge.requestShutdown()`, que encerra a sessão atual via `AccountManager.shutdown()` (**não** `logout()` — preserva o token local para o próximo `python -m frontend` continuar logado) e só então deixa a janela fechar — sem tasks órfãs.

## Contas locais (v0.9) — AccountManager

**Status: implementado.** `AccountManager` (`app/account_manager.py`) é a fachada de contas — login, registro, sessão persistida entre execuções, e dona do ciclo de vida de `JarvisCore`/`JarvisApplication` por usuário logado (memória isolada por conta). Só o `JarvisBridge` fala com ela; QML nunca importa nada daqui.

```
AccountManager
    ├── UserRepository        (services/user_repository.py — SQLite, hash de senha)
    ├── SessionRepository     (services/session_repository.py — token opaco + expiração)
    ├── ConversationRepository (services/conversation_repository.py — chats persistidos)
    ├── session_store         (services/session_store.py — token local persistido, nunca a senha)
    ├── memory_migration      (services/memory_migration.py — memória legacy -> primeira conta)
    └── JarvisCore + JarvisApplication   (só existem enquanto alguém está logado)
```

**Banco de dados**: SQLite via `sqlite3` da biblioteca padrão (`services/local_database.py`), sem ORM — tabelas `users`, `sessions`, `conversations`, `messages` e (v1.0) `email_verification_tokens`, com `PRAGMA foreign_keys = ON` e toda query parametrizada (`?`), nunca string interpolada. Vive em `settings.db_path` (`data/jarvis.db`), fora do Git.

## Configuração via `.env` (v1.1)

`config/env_loader.py`, chamado **no topo de `config/settings.py`**, antes da definição do dataclass. Isso não é detalhe de estilo: os defaults de `Settings` são `os.environ.get(...)` avaliados quando a classe é criada — carregar o `.env` depois disso não teria efeito nenhum. Colocando a chamada lá, nenhum ponto de entrada (`main.py`, `python -m frontend`, script) consegue esquecer.

Precedência: **variável já no ambiente > `.env` > default do código** (`override=False`). O `.env` nunca sobrescreve o que o usuário definiu explicitamente.

Usa `python-dotenv` (declarado em `requirements.txt` a partir da v1.1 — já vinha instalado como dependência transitiva, e depender disso sem declarar é frágil), com um fallback mínimo interno para o caso do pacote não estar presente: o JARVIS abre de qualquer jeito.

**Testes são herméticos.** `load_project_env()` se recusa a ler o `.env` quando detecta um runner de teste (`unittest`/`pytest` em `sys.modules`, ou `JARVIS_DISABLE_DOTENV=1`) **e** remove as variáveis sensíveis do processo. Sem isso, a suíte passaria a enxergar as credenciais reais do desenvolvedor e poderia gastar requisição ou enviar e-mail de verdade. A detecção por `sys.modules` é o que garante isso independente de ordem de import — `tests/__init__.py` sozinho roda tarde demais, porque um módulo de teste pode importar `config.settings` antes de `tests.helpers`.

**Valores em branco são tolerados**: `_env_int`/`_env_float`/`_env_flag` caem no default. Necessário porque o `.env.example` traz `JARVIS_SMTP_PORT=` etc. vazios de propósito — antes disso, copiar o arquivo de exemplo (que é o que a documentação manda fazer) derrubava o app com `int('')` já no import.

**Migrações versionadas (v1.0)**: o schema é versionado por `PRAGMA user_version` e evolui por uma lista ordenada de migrações, **uma transação por migração**. Se uma falhar, o `ROLLBACK` devolve o banco ao estado anterior e o erro sobe como `MigrationError` — nunca fica meio-migrado, e **nunca** apagamos/recriamos o banco para resolver divergência de schema. A migração v1 → v2 (v0.9 → v1.0) adiciona e-mail/verificação/contadores de força bruta e converte `sessions.token` (texto puro) em `sessions.token_hash` calculando o SHA-256 dos tokens existentes em Python — de modo que **nenhuma sessão ativa foi invalidada** (validado contra o banco real: o auto-login continuou funcionando após a migração).

**Senha**: nunca armazenada em texto puro. `services/password_hashing.py` usa `hashlib.scrypt` (biblioteca padrão, um dos KDFs recomendados pelo OWASP Password Storage Cheat Sheet junto com Argon2id) — salt aleatório de 16 bytes via `secrets`, custo `N=2^15, r=8, p=1`, comparação em tempo constante via `hmac.compare_digest`, formato auto-descritivo (`scrypt$n$r$p$salt$hash`). Escolhido em vez de `bcrypt`/`argon2-cffi` para não somar dependência externa nova — `scrypt` já é stdlib.

**Sessão persistida sem guardar a senha**: `SessionRepository.create_session()` gera um token opaco (`secrets.token_urlsafe(32)`) com expiração (30 dias). **v1.0: o banco guarda só o SHA-256 do token**, nunca o token em si — quem obtiver uma cópia do `jarvis.db` não consegue se passar por um usuário logado (ver [`docs/security.md`](security.md)). O mesmo token é espelhado localmente (`services/session_store.py`, `data/session.local`) para sobreviver a um fechar/reabrir do JARVIS — cifrado via Windows DPAPI (`win32crypt.CryptProtectData`/`CryptUnprotectData`, já uma dependência transitiva de `pyttsx3`) quando disponível, com fallback degradado (texto puro, mas ainda fora do Git) se `win32crypt` não existir no ambiente. `AccountManager.try_auto_login()` valida esse token uma vez, na inicialização; token ausente/inválido/expirado só mostra a tela de login normalmente, sem erro.

**Logout vs. shutdown** — dois encerramentos deliberadamente diferentes: `logout()` invalida a sessão no banco e apaga o token local (próxima abertura pede login de novo); `shutdown()` (fechar a janela) só derruba a Application Layer em RAM, preservando o token — a próxima execução continua logada. Nenhum dos dois apaga conta, chats ou memória.

**Isolamento entre usuários — reforçado na query, não só na UI**: todo método de `ConversationRepository` recebe e filtra por `user_id` no `WHERE` (e um `_owns()` privado antes de qualquer mutação) — mesmo um bug hipotético em uma camada acima que passasse o ID errado não vazaria dado entre contas. Testado explicitamente com dois usuários reais (`tests/test_account_manager_conversations.py::test_user_a_cannot_see_or_access_user_b_conversations`, `tests/test_account_manager_auth.py::test_session_token_does_not_cross_authenticate_between_users`).

**Chats persistidos**: `ConversationRepository` guarda `Conversation`(id, user_id, title, created_at, updated_at) e `Message`(id, role, content, timestamp) — nunca um tipo do Claude Agent SDK. Título derivado das primeiras palavras da primeira mensagem (`derive_title()`, sem IA — nenhum Claude real ainda). A persistência em si é **orientada a eventos**: `AccountManager` se inscreve na própria fila de `JarvisApplication.subscribe()` (mesmo mecanismo que `JarvisBridge` usa) e salva mensagens novas ao ver `message.received`/`response.completed` — nenhuma mudança foi necessária em `Orchestrator`/`JarvisCore` para isso.

**Histórico visual vs. sessão real do Claude — distinção importante**: `JarvisApplication.load_conversation_history()` (chamado ao abrir uma conversa salva) só repovoa o `Conversation` em RAM para exibição — **não** reconecta uma sessão real do `ClaudeSDKClient` com aquele contexto (não existe Claude real ainda para reconectar). Isso está documentado explicitamente no docstring do método para nunca ser confundido no futuro com "restaurar memória de conversa" de verdade.

**Memória por conta**: cada usuário tem `data/users/<user-id>/memory/{profile.md,preferences.md}` (`services/memory_migration.py::user_memory_dir()`) — nunca o username diretamente no caminho (evita path traversal via nome de usuário malicioso; o ID interno, um UUID, é que vira nome de pasta). A memória legacy pré-contas (`memory/profile.md`/`memory/preferences.md`, v0.1-v0.8) é migrada — **copiada, nunca movida/apagada** — para a primeira conta criada no ambiente (`UserRepository.has_any_user()` decide isso antes de criar a conta), e só se o destino ainda não tiver arquivo próprio (idempotente, nunca sobrescreve).

**Memória de longo prazo por usuário (v1.1)**: `services/long_term_memory.py`. Separada do histórico de conversa de propósito — mensagem pertence a UMA conversa; memória pertence ao USUÁRIO e atravessa todas:

```
User
├── Conversation A   (messages — de um chat só)
├── Conversation B
└── LongTermMemory   (user_memories — fatos que valem em qualquer chat)
```

- **Extração conservadora e local**: heurística por padrões (`meu nome é X`, `prefiro Y`, `lembre que Z`), sem nenhuma chamada de IA — memorizar não pode custar dinheiro nem depender de rede. A maioria das mensagens não vira memória, e isso é o esperado: "Quanto é 5+5?" nunca vira. Perguntas são explicitamente excluídas ("Qual é meu nome?" contém "meu nome" mas não afirma nada). O casamento é feito sobre o texto **sem acento** (dobra 1:1 que preserva índices), então "Meu nome e Davi" funciona igual a "Meu nome é Davi" — mas o recorte vem do original, preservando acentuação.
- **Só mensagens do usuário** viram memória. Memorizar o que a IA respondeu realimentaria alucinação.
- **Deduplicação por `dedup_key`** (conteúdo normalizado) com `UNIQUE(user_id, dedup_key)` — "Meu nome é Davi" e "me chamo Davi" não geram duas entradas.
- **NÃO mandamos todos os chats para o provider.** Só as memórias mais relevantes (ranking local por sobreposição de palavras, teto de 20) entram no contexto — privacidade, custo, tokens e relevância.
- **Isolamento**: `AccountManager._memory_context_for()` sempre usa `self._current_user.id`, nunca um ID vindo da Application Layer ou do frontend. Toda query é escopada por `user_id`, inclusive `forget()`.
- **A mensagem persistida continua limpa**: a memória entra só no texto enviado à IA (`JarvisApplication._build_ai_input`), nunca no que é exibido/gravado. Comandos nunca são aumentados — prefixar algo faria o `CommandRegistry` deixar de reconhecê-los.

**Verificação de e-mail (v1.0)**: `services/email_verification_service.py` + `_repository.py` + `services/email_service.py` (SMTP genérico, nunca amarrado a um provedor específico). Código de 6 dígitos, hash `scrypt` no banco (reusando `password_hashing.py` — nenhuma criptografia nova), expira em 5 min, reenvio após 60s, uso único, novo invalida o anterior, máximo de 5 tentativas. **Todas as regras de tempo são validadas no backend** contra timestamps persistidos — o QML só decrementa a exibição, então fechar e reabrir o app mostra o tempo real restante. Sem SMTP configurado, o serviço reporta `EMAIL_SERVICE_NOT_CONFIGURED` em vez de fingir envio. Uma conta não verificada **continua funcionando** (a verificação existe para tornar a conta recuperável no futuro, não como paywall — bloquear o app numa conta local sem backend só puniria quem não configurou SMTP).

**FREE/PRO sem billing**: `app/entitlements.py` — `Plan` (`app/models.py`) mais `Entitlements`/`entitlements_for(plan)`, um único ponto de resolução em vez de `if user.plan == Plan.PRO` espalhado pelo projeto. Hoje PRO só difere em `max_conversation_messages` (400 vs. 200) — tudo o resto (`advanced_tools`, `multi_agent`) é `False` para os dois planos, documentado como PLANEJADO. Sem Stripe, Pix, cartão, checkout ou assinatura.

## Voice Foundation (v0.7) — VoiceService

**Status: implementado.** `VoiceService` (`services/voice_service.py`) vive em `JarvisApplication`, ao lado (não dentro) de `JarvisCore` — é uma capability opcional do frontend, não parte do raciocínio do Core/Orchestrator:

```
JarvisApplication
    ├── start_listening() / stop_listening_and_transcribe() / cancel_listening()
    ├── speak(text) / stop_speaking() / set_voice_output_enabled(bool)
    ↓
VoiceService (services/voice_service.py)
    ├── SpeechToTextService          (services/stt_service.py)
    │       ├── UnavailableSTTService     (sem mic/modelo/dependência)
    │       └── VoskSTTProvider           (services/vosk_stt_provider.py — Vosk, offline)
    └── TextToSpeechService          (services/tts_service.py)
            ├── UnavailableTTSService     (sem engine)
            └── SapiTTSProvider           (services/sapi_tts_provider.py — SAPI5/Windows, offline)
```

O QML **nunca** fala com STT/TTS diretamente — só com o Bridge, que só fala com `JarvisApplication` (mesma regra do resto da Application Layer). `VoiceService` não conhece Qt: emite no mesmo `EventBus` interno que `JarvisCore` já usava (`voice.listening.started/stopped`, `voice.transcription.started/completed/failed`, `voice.speaking.started/stopped/failed`, `voice.level`), relayado como `AppEvent` por `JarvisApplication` exatamente como `state.changed` já era.

**Providers escolhidos:**
- **STT: [Vosk](https://alphacephei.com/vosk/)** — offline, Apache 2.0, modelos pequenos com pt-BR, sem GPU. Preferido a soluções baseadas em Whisper por não puxar `torch`/toolchains pesadas como dependência transitiva.
- **TTS: SAPI5 do Windows via `pyttsx3`** — zero modelo para baixar (usa vozes já instaladas no Windows), MPL-2.0. Escolhe automaticamente uma voz pt-BR se existir.
- **Microfone: `sounddevice`** (MIT, wraps PortAudio) — usado só dentro de `VoskSTTProvider`.

**Nenhum modelo é baixado automaticamente.** `create_stt_service(settings)` procura o modelo Vosk em `settings.stt_model_path` (`data/models/vosk/vosk-model-small-pt/`, fora do Git — movido de `voice_models/` no v0.9); se ausente, cai para `UnavailableSTTService` sem quebrar nada.

**Estados**: `app/state.py` ganhou `PROCESSING_SPEECH` (`LISTENING`/`SPEAKING` já existiam desde o v0.5, só nunca tinham sido produzidos em runtime). `JarvisApplication` chama `self._core.set_state(...)` diretamente ao redor das chamadas ao `VoiceService` — reaproveita toda a infraestrutura de `state.changed` já existente, sem um "voice state machine" paralelo.

### Diagnóstico e correção do microfone (v0.9)

**O problema real, confirmado por leitura direta do código (não presumido)**: `create_stt_service()` (v0.7/v0.8) tinha um único `try/except` genérico ao redor de "importar Vosk e construir o provider" — modelo ausente, dependência ausente, e qualquer outra falha caíam todas no mesmo `UnavailableSTTService()`, sem distinção. O HUD só sabia "voz indisponível", nunca por quê — exatamente o sintoma relatado ("VOICE UNAVAILABLE" genérico).

**Causa raiz neste ambiente de desenvolvimento**: nenhum modelo Vosk jamais foi baixado (por desenho — nunca automático), então `settings.stt_model_path` sempre apontava para um diretório inexistente. Confirmado diretamente contra `config.settings.settings` real neste ambiente.

**Correção — dois problemas separados, dois fixes**:

1. **Diagnóstico**: `services/stt_service.py` ganhou `STTStatus` (`READY`/`SETUP_REQUIRED`/`NO_MICROPHONE`/`UNAVAILABLE`). `create_stt_service()` agora checa `settings.stt_model_path.is_dir()` **antes** de tentar importar/construir o provider, devolvendo `UnavailableSTTService(STTStatus.SETUP_REQUIRED)` especificamente quando o modelo está ausente — em vez de um `except` genérico que não sabia dizer "é o modelo" de "é outra coisa". `VoskSTTProvider` (quando o modelo existe) resolve `READY` vs. `NO_MICROPHONE` checando `sounddevice.query_devices(kind="input")`.
2. **Bug latente real, corrigido antes de virar sintoma**: `VoskSTTProvider` (v0.7) capturava sempre a 16 kHz, presumindo que todo microfone suporta essa taxa nativamente — o que não é garantido (drivers/dispositivos variam). `services/vosk_stt_provider.py` agora detecta o sample rate nativo do dispositivo padrão (`sd.query_devices(kind="input")["default_samplerate"]`) e abre a captura nessa taxa, reamostrando em software para os 16 kHz que o Vosk espera (`_LinearResampler`, interpolação linear com estado preservado entre blocos — sem `numpy`/`scipy`, só o módulo `array` da stdlib). Verificado com uma onda senoidal sintética: 44100 Hz → 16000 Hz produz exatamente a contagem de amostras esperada. Confirmado contra hardware real neste ambiente: o microfone padrão detectado (`sd.query_devices(kind="input")`) roda nativamente a 44100 Hz — exatamente o caso que quebraria sob a suposição fixa de 16 kHz.

**Fluxo de instalação do modelo — `VoiceModelManager`** (`services/vosk_model_manager.py`): sabe `is_installed`, `model_path`, `info()` (nome/idioma/tamanho aproximado/licença/origem) e `download_and_install()`. Nunca baixa nada sozinho — só existe para responder a uma ação explícita do usuário no HUD (clicar em "Baixar modelo de voz (~45 MB)" no `VoiceSetupOverlay`, aberto quando o `MicButton` é clicado em `SETUP_REQUIRED`). Origem: `alphacephei.com` (mantenedores oficiais do Vosk), licença Apache 2.0.

**Download seguro**: HTTPS obrigatório (`download_and_install()` recusa qualquer URL que não comece com `https://`), `urllib.request` da stdlib (não `requests`, para não depender de uma dependência transitiva não declarada), progresso real via `Content-Length`, cancelamento cooperativo checado a cada chunk, arquivo temporário dentro de `data/models/vosk/` (nunca fora), limpeza garantida em sucesso ou falha (`tempfile.TemporaryDirectory`). **Proteção contra Zip Slip**: cada entrada do `.zip` tem seu caminho resolvido e checado (`Path.resolve()` + `Path.is_relative_to(destino)`) **antes** de `extractall()` — uma entrada como `../../evil.txt` é rejeitada com `ModelDownloadError`, sem escrever nada fora da pasta do modelo. Testado com um `.zip` malicioso construído localmente (`tests/test_entitlements_and_voice_model_manager.py::test_extract_safely_blocks_path_traversal`), nunca baixado de um servidor real.

**MicButton — 5 estados, não mais "disponível/indisponível"**: `SETUP_REQUIRED` (clique abre o `VoiceSetupOverlay`), `READY` (clique inicia captura), `LISTENING` (clique para e transcreve), `PROCESSING` (clique não faz nada — nunca dispara uma segunda captura), `ERROR` (mic ausente ou falha real ao abrir o dispositivo — tooltip específica, nunca "indisponível" genérico).

**Privacidade**: o microfone só liga em resposta a uma ação explícita do usuário (clique ou `Ctrl+Space`); nenhum áudio é escrito em disco — `VoskSTTProvider` processa os frames PCM em streaming, direto da callback do PortAudio para o `KaldiRecognizer`, sem arquivo intermediário (nem temporário).

**Transcrição não vai para a IA sozinha**: o texto reconhecido chega só como payload do evento `voice.transcription.completed` — quem decide o que fazer com ele é o frontend (no HUD, cai no `InputBar` para revisão). Nenhuma tool real é acionada por voz, e não existe atalho que pule `PermissionService` (ver "Voz e permissões" abaixo).

**Fala automática opt-in**: `Settings.voice_output_enabled` (padrão `False`) controla se `send_message()` dispara `self.speak(raw_reply)` automaticamente após um `response.completed`. O HUD liga/desliga isso em runtime via `set_voice_output_enabled()`, sem precisar reiniciar.

**Concorrência com o chat**: `start_listening()`/`speak()` e `send_message()` se bloqueiam mutuamente (`AppErrorCode.JARVIS_BUSY`) — não é possível gravar/falar e mandar uma mensagem de texto ao mesmo tempo, mesma política de "uma requisição ativa por vez" já usada para o chat.

### Voz e permissões — princípio de segurança

Voz nunca dá autoridade adicional sobre o que já existiria digitando a mesma frase. O fluxo desenhado para quando ferramentas reais existirem é:

```
voz → STT → texto → intenção → tool → PermissionService → confirmação do usuário
```

Isto é: uma frase transcrita passa pelo **mesmo** `PermissionService` (`app/permissions.py`) que qualquer outra origem de comando — não existe (nem vai existir) um caminho que deixe voz executar uma ação sem a confirmação que um comando de texto exigiria.

## Camada de IA: AIService → ProviderRouter | ClaudeAgentProvider

**Status: implementado e ativo (v1.0).**

```
Orchestrator
    ↓
AIService                 (abstração — services/ai_service.py)
    ↓
    ├── ProviderRouterAIService  (v1.0 — services/provider_ai_service.py, com OPENROUTER_API_KEY)
    │       ↓
    │   ProviderRouter           (services/providers/router.py — decide provider/modelo/custo)
    │       ↓
    │   OpenRouterProvider       (services/providers/openrouter_provider.py)
    │
    ├── ClaudeAgentProvider      (services/claude_agent_provider.py — com ANTHROPIC_API_KEY)
    │       ↓
    │   ClaudeSDKClient          (claude_agent_sdk)
    │
    └── UnavailableAIService     (fallback — sem nenhuma chave; o app abre e o chat não quebra)
```

`create_ai_service(settings)` decide checando apenas a *presença* das chaves no ambiente, nesta precedência: **OpenRouter → Anthropic → indisponível**. OpenRouter vem primeiro porque é o caminho que passa pelo `ProviderRouter`, a camada que dá controle real de custo (`free_only`). O Orchestrator continua conhecendo só `AIService` — não sabe o que é um `ProviderRouter`, um `ClaudeSDKClient` ou uma chave de API.

**Por que um adaptador e não um provider novo no Orchestrator**: a interface `AIService` (`start`/`ask`/`close`) já era exatamente o contrato de "uma sessão de conversa". O `ProviderRouter` fala em requisições isoladas. `ProviderRouterAIService` guarda o histórico em RAM e o reenvia a cada chamada, transformando a API stateless da OpenRouter na sessão contínua que o resto do sistema espera (ver [`docs/providers.md`](providers.md)). Nenhuma camada acima mudou.

**Ruflo não participa desta cadeia.** Nenhuma chamada de IA do JARVIS passa por `agent_execute` — ver [`docs/ruflo-integration.md`](ruflo-integration.md) para o bug de model routing que motiva essa separação.

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

### Status do Claude Agent SDK: preservado, não ativado neste ambiente

A integração com o Agent SDK continua implementada e testada com fakes (`tests/test_claude_agent_provider.py`), mas **nenhuma chamada real** foi feita a ela: não há `ANTHROPIC_API_KEY` neste ambiente. Configurar a variável é suficiente para ativá-la, sem mudança de código — lembrando que, se `OPENROUTER_API_KEY` também existir, o OpenRouter tem precedência.

O caminho **OpenRouter**, ao contrário, foi validado de ponta a ponta com uma chamada real (ver [`docs/providers.md`](providers.md), seção "Smoke test manual real").

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
JarvisCore.build_memory_context()   # contexto controlado
    ↓
AIService.start(memory_context=...)
    ↓
ClaudeAgentProvider  →  system_prompt  →  Claude
```

O Core lê perfil e preferências via `MemoryService` e monta um contexto controlado, entregue **uma vez**, no `start()` da sessão (não a cada mensagem) — isso vai para o `system_prompt` da sessão, junto com a identidade de runtime. O Agent SDK **não** varre `memory/` sozinho: `setting_sources=[]` e a ausência de qualquer ferramenta de leitura de arquivo garantem isso. A memória continua somente leitura em runtime — nenhuma escrita acontece a partir de uma conversa.

## Responsabilidade de cada camada

### Usuário
Ponto de entrada de tudo: fala ou digita um pedido, e recebe respostas em texto e/ou voz.

### App / HUD — [`app/`](../app/), [`frontend/`](../frontend/)
**Status: implementado (terminal + HUD gráfico + Application Layer).**
Dois frontends hoje: o terminal (`app/terminal.py`), fino — só lê input, imprime output e trata encerramento (Ctrl+C, EOF, `/exit`) — e o HUD gráfico (`frontend/`, PySide6/QML, ver seção "Frontend/HUD" acima), os dois falando com `JarvisApplication`. O pacote `app/` também contém o núcleo de execução (`JarvisCore`, `Orchestrator`, `commands.py`, `state.py`) e a Application Layer (`application.py`, `models.py`, `conversation.py`, `status.py`, `permissions.py`). Voz (v0.7, ver seção "Voice Foundation" acima) é um recurso do HUD — o terminal não ganhou voz.

### Orquestrador JARVIS — [`app/orchestrator.py`](../app/orchestrator.py)
**Status: implementado.**
Decide o que fazer com cada entrada: comando interno (`CommandRegistry`, síncrono) ou mensagem comum (`AIService`, assíncrono) — devolvendo o texto cru da resposta (formatação de apresentação, como o prefixo "JARVIS: ", é responsabilidade de `JarvisApplication`/terminal). Integra IA com estados (`THINKING` → `IDLE`, ou `THINKING` → `ERROR` → `IDLE` em falha) e eventos internos (`ai.request.started/completed/failed`). Não conhece `claude_agent_sdk`, nem tipos internos do SDK, nem autenticação, nem `JarvisApplication` (a dependência é de cima para baixo). Chamar ferramentas, pedir confirmação e usar subagentes/Ruflo continuam **planejados**.

### Claude Code
**Status: usado como agente de desenvolvimento (fora do runtime); arquitetura de runtime preparada via `ClaudeAgentProvider`, não ativada neste ambiente.**
Como agente de desenvolvimento, interpreta pedidos, raciocina, decide quais ferramentas/skills/subagentes usar, e produz respostas — consultando e atualizando a memória (`memory/`, `projects/`, `daily/`) conforme `CLAUDE.md`. Como motor de runtime do assistente, a integração usa o **Claude Agent SDK** (`claude-agent-sdk`, pacote Python `claude_agent_sdk`) via `ClaudeSDKClient`, não a API de mensagens tradicional.

### Serviços internos — [`services/`](../services/)
**Status: implementado.**
- `memory_service.py` — leitura somente-leitura de `memory/profile.md` e `memory/preferences.md`, com tratamento de erro para arquivo ausente/ilegível. Escrita de memória continua planejada.
- `event_bus.py` — pub/sub síncrono em memória, **interno** (`Core`/`Orchestrator`). Eventos hoje: `jarvis.started`, `jarvis.stopped`, `state.changed`, `message.received`, `message.responded`, `command.executed`, `ai.connecting`, `ai.connected`, `ai.disconnected`, `ai.request.started`, `ai.request.completed`, `ai.request.failed`, `permission.requested`, `permission.resolved`. Não confundir com o `AppEvent` de `JarvisApplication` (ver seção "Application Layer") — este é interno, aquele é o contrato estável para frontends.
- `ai_service.py` — interface `AIService` (agora com lifecycle assíncrono `start`/`ask`/`close`, mais `session_active` e `backend_name`), `UnavailableAIService`, e `create_ai_service(settings)`. Não importa o Agent SDK.
- `claude_agent_provider.py` — `ClaudeAgentProvider`: único módulo que importa `claude_agent_sdk`. Erros oficiais do SDK (`CLINotFoundError`, `CLIConnectionError`, `ProcessError`, `CLIJSONDecodeError`, `ClaudeSDKError`, e qualquer outra exceção inesperada) viram `ClaudeAgentProviderError` com mensagem segura — nunca uma exceção crua do SDK escapa para o Orchestrator.
- `runtime_identity.py` — instruções de persona do JARVIS (ver seção acima).
- `voice_service.py`, `stt_service.py`, `tts_service.py` (v0.7) — coordenação de voz e as abstrações STT/TTS (ver seção "Voice Foundation" acima). `vosk_stt_provider.py`/`sapi_tts_provider.py` são os únicos módulos que importam `vosk`/`sounddevice`/`pyttsx3`, respectivamente.

### Ferramentas / Skills / MCP / Subagentes — [`tools/`](../tools/), [`.claude/skills/`](../.claude/skills/), [`.claude/agents/`](../.claude/agents/), [`integrations/`](../integrations/)
**Status: não implementado (planejado).**
O conjunto de capacidades que o Claude poderá invocar para agir: ferramentas locais classificadas por risco (READ / ACTION / DANGEROUS — ver [`tools/README.md`](../tools/README.md)), Skills reutilizáveis, servidores MCP para integrações externas, e subagentes especializados em tarefas específicas. Nesta v0.3, todas as ferramentas do Agent SDK estão explicitamente desligadas (ver seção "Ferramentas e permissões" acima).

### Sistema operacional / APIs / serviços
**Status: não implementado (planejado).**
A camada mais externa: o Windows em si (arquivos, processos, automações) e serviços/APIs de terceiros acessados via `integrations/`. Toda ação aqui deve passar pela classificação de risco de `tools/` antes de ser executada.

## Fluxos paralelos

- **Claude Code ↔ Memory**: `MemoryService` permite leitura somente-leitura de `memory/profile.md` e `memory/preferences.md` a partir do código (**implementado**) e alimenta o contexto entregue à IA (**implementado**, ver seção acima); o Claude Code, como agente de desenvolvimento, também lê essa memória diretamente conforme `CLAUDE.md` (**implementado**, fora do runtime do Core). Escrita programática de memória é **planejada**.
- **Hooks → App/HUD**: **planejado**.
- **Voice STT → Application**: **implementado (v0.7)** — o texto reconhecido chega como evento (`voice.transcription.completed`) e cai no campo de entrada do HUD; não é enviado à IA automaticamente, e não passa pelo Orchestrator (não é tratado como comando nem como intenção — é só texto para o usuário revisar).
- **Application → TTS**: **implementado (v0.7)** — `voice_output_enabled` (desligado por padrão) faz `send_message()` falar a resposta automaticamente após `response.completed`.

## Sistema de permissões (ferramentas do computador)

Nenhuma ferramenta com efeito no mundo real deve ser executada sem passar pela classificação de risco descrita em [`tools/README.md`](../tools/README.md):

- **READ**: livre.
- **ACTION**: pode exigir confirmação, dependendo do impacto.
- **DANGEROUS**: sempre exige confirmação explícita do usuário.

**Status: classificação em si apenas documentada; fundação de modelo implementada.** O JARVIS não executa nenhuma ação classificável nessas categorias — não há ferramentas de computador nem MCP habilitados nesta versão (ver "Ferramentas e permissões" acima para o mecanismo específico do Agent SDK). `app/permissions.py` (`PermissionService`, `PermissionRequest`, `RiskLevel`, `PermissionStatus`) já implementa o modelo de dados e o fluxo `request → approve/deny` em memória, com eventos (`permission.requested`/`permission.resolved`) — mas **não está conectado a nenhuma ferramenta real**. Existe só para o futuro HUD conseguir mostrar "JARVIS deseja realizar uma ação — [Permitir] [Negar]" sem exigir redesenho do backend quando ferramentas reais existirem.

Isso vale também para voz (v0.7, ver "Voz e permissões" na seção "Voice Foundation" acima): uma frase transcrita é só texto, entra pela mesma porta que qualquer mensagem digitada, e não existe nem vai existir um atalho que deixe comando de voz executar uma ação sem passar por `PermissionService`.

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

O Claude Agent SDK é assíncrono (`ClaudeSDKClient` usa `async`/`await`). Para integrá-lo sem uma ponte sync/async artificial, `JarvisCore.start/stop/handle_input`, `Orchestrator.handle/_handle_message`, `JarvisApplication` (praticamente por inteiro) e `app/terminal.run` também são `async`. `main.py` cria **um único** event loop (`asyncio.run`) para toda a execução — não é recriado por mensagem, nem por requisição cancelada. `JarvisApplication.send_message()` envolve a chamada ao Core em uma `asyncio.Task` própria (`self._current_request_task`) especificamente para permitir cancelamento local (`Task.cancel()`) sem afetar o loop inteiro. O loop de terminal usa `input()` de forma bloqueante entre mensagens; como não há nenhuma outra tarefa concorrente disputando o loop nesta versão, isso não é um problema. Comandos internos (`/help`, `/status`, etc.) continuam síncronos, pois são instantâneos. Este design já comporta, sem reescrita: streaming (iterar `receive_response()` incrementalmente e emitir `response.delta`), uma GUI/HUD chamando `JarvisApplication` a partir do próprio loop assíncrono dela, e futuras ferramentas assíncronas.

## O que já existe vs. o que é planejamento

**IMPLEMENTADO NO v0.5–v1.0:**
- **IA real conectada (v1.0)**: `AIService → ProviderRouter → OpenRouter`, `free_only` por padrão, histórico reenviado (API stateless), erros de provider normalizados, metadados técnicos fora do texto da mensagem — ver "Camada de IA" acima
- **Verificação de e-mail (v1.0)**: código de 6 dígitos, 5 min, reenvio 60s, uso único, rate limit — validado no backend
- **Hardening de auth (v1.0)**: token de sessão como hash, backoff de força bruta, anti-enumeração (mensagem e timing)
- **Migração de schema versionada e transacional (v1.0)** — dados v0.9 preservados, sessões não invalidadas
- **Sanitização de contexto (v1.0)**: data minimization + redação de segredos antes de qualquer coisa sair da máquina
- Contas locais (v0.9): registro/login/logout, sessão persistida entre execuções (token opaco + Windows DPAPI), isolamento por `user_id` reforçado na query, hash de senha via `scrypt` — ver seção "Contas locais" acima
- Sidebar retrátil e chats persistidos em SQLite (v0.9): criar/listar/ordenar/buscar/renomear/excluir conversa, carregar conversa antiga (histórico visual, não sessão real do Claude), título derivado da primeira mensagem
- Memória por conta com migração controlada da memória legacy (v0.9) — original nunca apagado/movido
- Fundação FREE/PRO sem billing (v0.9) — `app/entitlements.py`
- Microfone corrigido de verdade (v0.9): `STTStatus` (`READY`/`SETUP_REQUIRED`/`NO_MICROPHONE`/`UNAVAILABLE`), `VoiceModelManager` com download seguro (HTTPS, progresso, cancelamento, proteção Zip Slip), captura no sample rate nativo do dispositivo com reamostragem em software, `MicButton` com 5 estados — ver "Diagnóstico e correção do microfone" acima
- HUD Overhaul (v0.8): design system consolidado em `Theme.qml`, núcleo de IA v3 (anel completo + arcos + segmentado + nós orbitais, ondas de saída em SPEAKING, transições orgânicas em todo estado), layout que escala em monitores grandes (250-520px, baseado em `min(largura, altura)` da janela) sem ficar minúsculo, boot em etapas (~1,3s), breakpoint único para status compacto, borda de janela sutil — preservado integralmente no v0.9 (ver [`frontend/README.md`](../frontend/README.md))
- Voz (v0.7, corrigida no v0.9): push-to-talk (STT via Vosk, offline), síntese de fala (TTS via SAPI5/Windows, offline), `VoiceService` na Application Layer, estados `LISTENING`/`PROCESSING_SPEECH`/`SPEAKING`, indicador de nível de voz real, botão de microfone e controle de fala automática no HUD
- HUD gráfico (`frontend/`, PySide6/QML, `python -m frontend`) — chat com indicador de resposta pendente, status, cancelamento, overlay de permissão (v0.6: bug de visibilidade corrigido; v0.8: ênfase extra para DANGEROUS; v0.9: tela de login/registro antes do HUD)
- `MessageListModel.update_content()` (v0.6) — ponto de extensão pronto para streaming futuro, não conectado a nenhum evento real ainda
- `JarvisBridge` (`frontend/bridge.py`) — ponte fina, orientada a eventos (sem polling), entre QML e `AccountManager`/`JarvisApplication` (auth-first desde o v0.9)
- Application Layer (`JarvisApplication`, `app/application.py`) — fronteira estável entre Core e qualquer frontend (terminal e HUD), incluindo voz
- Modelos de domínio sem dependência do Agent SDK (`app/models.py`): `Message`, `AssistantResponse`, `StatusSnapshot`, `AppEvent`, `PermissionRequest`, `TranscriptionResult`, `Plan`, `User`, `ConversationSummary`, etc.
- Histórico de conversa em runtime (`app/conversation.py`), separado e nunca confundido com `memory/`
- Stream de eventos para consumidores externos (`subscribe()`/`unsubscribe()`/`events()`), sem WebSocket/servidor
- Status consolidado com fonte única (`app/status.py`), usado tanto por `/status` quanto por `get_status()`, incluindo campos de voz
- Política de concorrência (uma requisição ativa por conversa — chat OU voz —, rejeição limpa) e cancelamento (`asyncio.Task.cancel()`)
- Erros de domínio estruturados para a interface (`AppErrorCode`: `AI_UNAVAILABLE`, `JARVIS_BUSY`, `INTERNAL_ERROR`, `MICROPHONE_UNAVAILABLE`, `STT_NOT_READY`, `TTS_UNAVAILABLE`, `VOICE_CANCELLED`)
- "Nova conversa" (`/new`, alias `/reset` no terminal; "+ Novo chat" na sidebar do HUD): limpa histórico runtime e reinicia a sessão de IA, sem tocar `memory/`
- Fundação de permissões em memória (`app/permissions.py`), com UI de overlay pronta no HUD — não conectada a ferramentas reais; voz não a contorna
- Terminal consumindo `JarvisApplication` diretamente (sem contas — ver "Estado atual em uma frase")
- Tudo o que já era v0.3/v0.4 (Claude Agent SDK, lifecycle, fallback, memória somente-leitura, estados, event bus interno)
- 300 testes automatizados, todos offline (mocks/fakes, SQLite temporário, sem chamada real de IA/e-mail/rede, sem microfone/TTS/download real, incluindo smoke test de QML offscreen — auth e HUD — e estados visuais simulados)
- Core/Application/HUD funcionais sem qualquer API key configurada e sem qualquer dependência de voz instalada

**PREPARADO, MAS NÃO ATIVADO:**
- Conexão real com Claude via Agent SDK (arquitetura pronta e testada com fakes; falta `ANTHROPIC_API_KEY` no ambiente — o caminho OpenRouter, esse sim, foi validado com chamada real)
- Streaming real token-a-token (contrato de eventos existe e o HUD sabe reagir; ver `docs/providers.md` para por que a v1.0 entrega resposta completa e não fez streaming falso)
- Groq/Gemini/Mistral/NVIDIA: registry `NOT_IMPLEMENTED`, sem classe real
- Sincronização de contas/chats na nuvem (arquitetura local-first, mas não impede isso no futuro)
- Cobrança real do plano PRO (`app/entitlements.py` já resolve capacidades por plano; sem Stripe/Pix/checkout)

**PLANEJADO:**
- Streaming token-a-token de verdade
- Providers adicionais reais pelo mesmo router (incluindo Anthropic)
- Backend/cloud, auth remota, recuperação de senha e billing real
- Wake word / escuta permanente (por enquanto é só push-to-talk, deliberadamente)
- Permissões interativas de verdade (overlay do HUD já existe; falta conectar a ferramentas reais)
- Ferramentas de computador (READ/ACTION/DANGEROUS conectado à execução) — voz não terá atalho para isso, ver "Voz e permissões"
- MCP
- Subagentes especializados
- **Ruflo** / orquestração multiagente — inclusive uma futura representação visual ("painel AGENTS") no HUD, não implementada
- Skills em runtime
- Memória avançada (embeddings, busca semântica, memória de curto/longo prazo)
- Tela de configurações, temas alternativos, seleção de dispositivo de microfone/voz na UI, empacotamento como aplicativo Windows instalável

Qualquer trabalho futuro deve atualizar este documento se a arquitetura real divergir do que está descrito aqui.
