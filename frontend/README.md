# frontend/

O HUD do JARVIS — interface gráfica real do projeto, introduzida no v0.5,
refinada visualmente no v0.6 (HUD Refinement / UX Foundation), com voz desde
o v0.7 (Voice Foundation), e com um segundo refinamento visual profundo no
**v0.8 (HUD Overhaul / UX 2.0)**. PySide6 (Qt for Python) + Qt Quick/QML. O
terminal (`python main.py`, `app/terminal.py`) continua existindo, separado
e inalterado — este é um segundo frontend, não uma substituição, e não
ganhou voz (é um recurso específico do HUD).

**v0.8 não muda a arquitetura do v0.5/v0.6/v0.7** (HUD → Bridge →
JarvisApplication continua igual, nenhuma capability nova) — é só
qualidade visual: design system consolidado, núcleo de IA v3, layout que
escala em monitores grandes, boot mais elaborado, e acabamento em
praticamente todo componente. Ver "O que mudou no v0.8" abaixo para o
resumo, e cada seção de componente para o detalhe.

## Como executar

```powershell
pip install -r requirements.txt
python -m frontend
```

Sem `ANTHROPIC_API_KEY` configurada, o HUD abre normalmente e mostra
`AI OFFLINE` — não trava, não pede credencial, não finge estar conectado.
`requirements.txt` já inclui as dependências de voz (`vosk`, `sounddevice`,
`pyttsx3`); a fala (TTS) funciona assim que elas estão instaladas, mas o
microfone (STT) só liga depois de baixar manualmente o modelo Vosk — ver
seção "Voz" abaixo. Sem o modelo, o botão de microfone aparece desabilitado
e o resto do HUD funciona normalmente.

## Arquitetura

```
JARVIS HUD (PySide6 / QML)
        │
        ↓
Frontend Bridge (frontend/bridge.py)
        │
        ↓
JarvisApplication (app/application.py)
        │              \
        ↓                → VoiceService (services/voice_service.py)
JarvisCore → Orchestrator      ├→ SpeechToTextService (STT)
        │                      └→ TextToSpeechService (TTS)
        ↓
AIService / MemoryService / Services
```

O QML **nunca** importa nada de `services/`, `app/core.py`,
`app/application.py`, o Claude Agent SDK, `vosk`, `sounddevice` ou
`pyttsx3` — só conhece `bridge`, exposto como propriedade de contexto do
QML (`engine.rootContext().setContextProperty("bridge", bridge)` em
`frontend/launcher.py`). `VoiceService` também não conhece Qt: fala só com
`SpeechToTextService`/`TextToSpeechService` (abstrações) e emite no mesmo
`EventBus` interno que `JarvisCore` já usava desde o v0.3 — quem traduz
tudo isso para QML continua sendo só o Bridge.

### `frontend/bridge.py` — `JarvisBridge`

Ponte fina entre QML e `JarvisApplication`: sem lógica de domínio, só
tradução. Expõe Properties Qt (`jarvisState`, `running`, `busy`,
`memoryAvailable`, `aiConfigured`, `aiBackend`, `aiSessionActive`,
`activeConversation`, `pendingPermission`, `messages`, `devMode`,
`canClose`, `voiceAvailable`, `ttsReady`, `voiceOutputEnabled`,
`voiceLevel`) e Slots (`sendMessage`, `cancelCurrentRequest`,
`newConversation`, `approvePermission`, `denyPermission`,
`requestShutdown`, `simulateState` — dev only —, `toggleListening`,
`cancelListening`, `stopSpeaking`, `setVoiceOutputEnabled`).

**Nunca faz polling.** Na inicialização, chama `JarvisApplication.subscribe()`
(síncrono — a fila é registrada imediatamente, sem depender de uma task
ainda rodar) e consome essa fila em uma única task de fundo
(`_consume_events`). Cada evento relevante dispara uma releitura pontual de
`get_status()`/`get_messages()` — nunca em loop/timer.

### `frontend/message_model.py` — `MessageListModel`

`QAbstractListModel` sobre `app.models.Message`. `sync()` faz insert
incremental quando a lista só cresceu no final (caso comum: nova
troca de mensagens) e reset completo quando encolhe/diverge (`/new`) —
preserva posição de scroll e permite animação de entrada no delegate.

**Preparação para streaming (v0.6, não ativada):** `update_content(id,
content)` atualiza o texto de uma linha já existente in-place (emite
`dataChanged` só do `ContentRole`, sem `beginResetModel`). Nada no backend
chama isso ainda — `response.delta` não existe (ver `docs/application-api.md`)
— é só o ponto de extensão pronto para quando streaming real existir: o
Bridge poderia chamar `update_content()` a cada delta em vez de esperar a
resposta completa. Coberto por `tests/test_message_model.py`.

### `frontend/launcher.py` — integração Qt + asyncio

Usa **`PySide6.QtAsyncio`**, a integração oficial do Qt for Python
([doc.qt.io/qtforpython-6/PySide6/QtAsyncio](https://doc.qt.io/qtforpython-6/PySide6/QtAsyncio/index.html)):
`QtAsyncio.run(main_coro, keep_running=True, quit_qapp=True, handle_sigint=True)`
funde o event loop do Qt e do asyncio em um só — sem thread extra, sem
polling, sem recriar o loop por mensagem, sem nenhuma dependência de
terceiros (`qasync` e afins) para isso. `PySide6` já vem com esse módulo;
nenhuma dependência adicional foi necessária.

**Encerramento limpo:** `Window.onClosing` intercepta o fechamento
(`closeEvent.accepted = false`) e chama `bridge.requestShutdown()`, que
cancela a requisição pendente (se houver), encerra a sessão de IA
(`JarvisApplication.stop()`) e só então deixa a janela fechar de verdade —
sem tasks órfãs, sem warnings de "Task was destroyed but it is pending".

## QML

```
frontend/qml/
├── Main.qml                 janela, layout, boot, resize, shortcuts
├── theme/
│   ├── Theme.qml             singleton — paleta, spacing, durações, tipografia
│   └── qmldir
└── components/
    ├── TitleBar.qml           barra de título própria (move/resize/min/max/close via API nativa do Qt)
    ├── WindowButton.qml       botão de controle de janela
    ├── JarvisCore.qml         núcleo animado — o elemento visual principal
    ├── ChatPanel.qml          lista de conversa, scroll inteligente, indicador de resposta pendente
    ├── MessageItem.qml        uma mensagem (bloco discreto, não bubble)
    ├── InputBar.qml           entrada multiline + microfone: [texto][MIC][SEND]
    ├── MicButton.qml          botão de microfone (idle/listening/processing_speech) — v0.7
    ├── Waveform.qml           nível de voz real durante LISTENING — v0.7
    ├── StatusPanel.qml        faixa de status real (CORE/MEMORY/AI/SESSION/VOICE + OUTPUT ON/OFF)
    ├── StatusIndicator.qml    ponto + label reutilizável
    ├── ActionButton.qml       botão genérico reutilizável (com tooltip embutido)
    └── PermissionOverlay.qml  pedido de permissão (READ/ACTION/DANGEROUS)
```

### `Theme.qml` — design system

Nenhuma cor/espaçamento/duração é hardcoded fora daqui — sempre `Theme.*`.
Paleta (v0.8 aumenta a profundidade, mesma direção desde o v0.6): fundo
quase preto com leve azul (`background #070A10`, topo do gradiente do fundo
`backgroundSecondary #0C1524` — nunca preto puro), superfícies em camadas
(`surface` → `surfaceElevated` → `surfaceGlass`, translúcida, usada no
`StatusPanel` para uma sensação de "vidro" sobre o HUD contínuo em vez de
card opaco flutuante), ciano frio (`cyan #5CE1E6`, e `primaryBright
#8FF3F7` para hover/ênfase) como primária, azul elétrico (`blue #4C8DFF`)
como secundária, violeta (`violet #9B6BFF`) como accent — sparse por
padrão, mas é a cor inteira de `LISTENING`. `surfaceHover` centraliza o
hover genérico de botões (`ActionButton`, `WindowButton`, `MicButton`), que
antes usava `rgba(1,1,1,x)` com opacidades ligeiramente diferentes espalhadas
pelos arquivos. Texto: `textPrimary` quase branco levemente azulado,
`textSecondary`/`textMuted`/`textFaint`. Fonte: `Segoe UI Variable` com
fallback automático do próprio Windows para `Segoe UI`. Espaçamento
(`spacingXs..Xl`), radius (`radiusSmall/Medium/Large`) e motion
(`durationFast/Normal/Slow/Boot`, `easingStandard`) continuam a mesma escala
desde o v0.6 — já eram consistentes, só ganharam mais um consumidor (as
camadas novas do Core).

Nomes já usados em ~12 arquivos QML (`cyan`, `blue`, `violet`,
`borderStrong` etc.) foram mantidos como estão — trocar por
`primary`/`secondary`/`accent`/`borderActive` em todo lugar seria só
churn/renomeação sem ganho visual real, então os tokens novos do v0.8
(`backgroundSecondary`, `surfaceGlass`, `surfaceHover`, `primaryBright`)
foram somados, não substituídos.

### `JarvisCore.qml` — o núcleo (AI Core) v3

O elemento visual central, desenhado inteiramente em QML (Qt Quick Shapes +
`Rectangle`s + animações nativas — **sem** GIF, vídeo ou pintura por frame
em Python, sem shader/blur). Camadas, de fora para dentro: glow ambiente (4
círculos concêntricos translúcidos), um **anel completo** (`energyRing` —
novo no v0.8; os outros três continuam arcos incompletos, então agora há
"anéis com funções visuais distintas": um fechado, três abertos, um
segmentado), 3 anéis com arcos incompletos (`PathAngleArc`, velocidades e
sentidos de rotação diferentes — alguns horário, outros anti-horário, para
dar profundidade), o **anel segmentado** (16 ticks), **partículas
orbitais** (6 pontos pequenos) e **nós orbitais** (3 pontos maiores com
halo próprio, girando como grupo — novo no v0.8, mais um nível de
profundidade sem virar sistema de partículas pesado), e o núcleo central
com uma camada intermediária extra de opacidade entre o halo e o ponto
central (simula um falloff radial sem precisar de `RadialGradient`/shader).

Reage a estado real via quatro propriedades (`state`, `aiConfigured`,
`aiSessionActive`, `voiceLevel`), vindas do Bridge — `idle | thinking |
working | listening | processing_speech | speaking | waiting_confirmation |
error` são os únicos estados válidos hoje (`app/state.py`):
- **IDLE** — respiração suave, rotações lentas independentes.
- **LISTENING** — cor violeta, e o núcleo passa a reagir a `voiceLevel`
  **de verdade**: o halo central e o anel externo pulsam com o nível real
  do microfone (throttled a ~20 updates/s na origem, suavizado por um
  `Behavior` de 90ms) — não é uma animação genérica, para de reagir no
  instante em que o áudio para.
- **PROCESSING_SPEECH** — pulso "alert" padrão (como THINKING); a transição
  de cor violeta → ciano ao sair de LISTENING já é orgânica de graça (o
  `Behavior on accent` de 420ms anima qualquer mudança de cor do Core, não
  só essa).
- **THINKING/WORKING** — rotação mais rápida, pulso mais forte (`alert`).
- **SPEAKING** — cor azul elétrica, mesmo pulso "alert", **e uma onda**
  (`speakingRipple`, novo no v0.8): um anel que expande e desaparece em
  loop, saindo do centro — a "saída de energia/informação" pedida, sem
  waveform (o SAPI5 não expõe amplitude real da fala, então nenhum medidor
  de nível é mostrado durante SPEAKING — só nesta onda simbólica).
- **WAITING_CONFIRMATION** — accent âmbar, e desde o v0.8 a rotação fica
  ainda mais lenta que IDLE (`ringSpeed` próprio) — "aguardando você", não
  "parado".
- **ERROR** — accent vermelho/coral com transição suave, pulso de alerta
  mais rápido e o anel interno **congela** (`running: !core.errored`); no
  v0.8 o anel segmentado também desacelera (não congela) — interrupção
  parcial em camadas diferentes, não tudo travando ou nada mudando — e
  retoma sozinho quando o estado sai de `error`.
- **Sem IA configurada/sessão inativa** — brilho reduzido (`dim = 0.72`),
  mas o núcleo nunca "morre" nem some.

Todas as transições de cor/opacity/scale usam `Behavior`, incluindo o halo
e o ponto central (não tinham no v0.7) — nenhuma mudança de estado é
instantânea.

### Layout, boot, resize, fullscreen

**Core escalável (v0.8):** o tamanho do Core é `clamp(250, 520, min(largura,
altura da janela) × 0.38)` — cresce de verdade em monitores grandes
(1920×1080, 2560×1440) em vez de bater um teto fixo cedo, e nunca fica
menor que 250px mesmo na janela mínima (1100×700). A coluna do Core ocupa
46% da largura da janela (era 42%).

**Breakpoint único (v0.8):** `window.width < 1250` ativa um modo compacto
no `StatusPanel` — some com `MEMORY`/`SESSION` (secundários) e mantém
`CORE`/`AI`/`VOICE` (prioritários), não um sistema de layout CSS-like.

**Boot em etapas, ~1,3s no total (v0.8: era ~1s no v0.6/v0.7):** title bar
(~60ms) → ponto semente no lugar do Core (imediato, some assim que o Core
começa a "energizar") → núcleo (~160ms) → região núcleo+chat (~420ms) →
status (~820ms) → input (~1000ms, mais a própria transição de opacity) — só
cosmético, nunca bloqueia o backend (que inicia em paralelo, de forma
totalmente independente). Nenhuma porcentagem de carregamento falsa em
nenhum momento.

`F11` alterna fullscreen, `Esc` sai dele. Janela sem moldura nativa
(`FramelessWindowHint`): mover usa `Window.startSystemMove()`, redimensionar
pelas bordas usa `Window.startSystemResize(edges)` — APIs oficiais do Qt,
sem cálculo manual de coordenadas. Uma borda externa de 1px ciano bem sutil
(`Theme` não define um token único pra isso — é um detalhe de acabamento só
do `Main.qml`) dá um acabamento sem afetar hit-testing/resize.

**Cantos arredondados — deliberadamente fora do v0.8:** arredondar uma
`Window` frameless exigiria torná-la translúcida
(`WA_TranslucentBackground`), o que arrisca o resize/Snap já validado desde
o v0.5 sem eu conseguir validar visualmente a mudança neste ambiente —
comportamento funcional > estética, conforme pedido.

**Limitação conhecida (desde o v0.6):** com moldura customizada, o menu de
Snap Layouts do Windows 11 (que aparece ao pairar o mouse sobre o botão
nativo de maximizar) não existe, já que o botão de maximizar é desenhado
por nós. Arrastar a janela até a borda da tela (Aero Snap) continua
funcionando normalmente, porque usa `startSystemMove()` (API real do SO).

## Permissões (fundação, sem ferramentas reais)

`PermissionOverlay.qml` reage a `bridge.pendingPermission` (populado via os
eventos `permission.requested`/`permission.resolved`, já existentes desde
o v0.4). A cor do cartão depende do `riskLevel` (`read` = ciano,
`action` = âmbar, `dangerous` = vermelho). Nenhuma ferramenta real dispara
isso ainda — só existe para o backend/frontend já saberem se comunicar
quando ferramentas reais existirem.

**Ênfase extra para DANGEROUS (v0.8):** borda do cartão mais grossa (2px em
vez de 1,5px) e um glow externo vermelho bem discreto atrás do cartão — só
para `riskLevel === "dangerous"`. READ/ACTION continuam com o tratamento
padrão. Nenhuma confirmação adicional foi implementada (não pedida nesta
versão) — é só reforço visual de que a decisão pesa mais.

**Bug do v0.5 corrigido no v0.6:** o overlay usava `request !== null`
(comparação estrita) para decidir se devia aparecer. Um
`Property("QVariant")` do Qt que devolve `None` do Python chega ao QML como
`undefined`, não como `null` — e em JavaScript `undefined !== null` é
sempre `true`. Resultado: o overlay ficava com `opacity: 1` (visível,
bloqueando clique) **mesmo sem nenhum pedido pendente**, mostrando um
cartão vazio logo na inicialização. Corrigido trocando as comparações
estritas por checagem "truthy" (`request ? ... : ...`, mesmo padrão já
usado em outros pontos do próprio arquivo), que trata `null` e `undefined`
da mesma forma. Coberto por `tests/test_qml_smoke.py`
(`test_permission_overlay_hidden_when_no_pending_request` e
`test_permission_overlay_visible_with_pending_request`).

## Voz (v0.7) — push-to-talk

Entrada (STT) e saída (TTS) de voz, ambas offline, ambas opcionais — sem
nenhuma delas instalada/configurada, o JARVIS funciona normalmente por
texto (voz é uma capability, não uma dependência).

### Providers escolhidos e por quê

**STT: [Vosk](https://alphacephei.com/vosk/)** (`services/vosk_stt_provider.py`).
Offline, Apache 2.0, modelos pequenos (dezenas de MB) com suporte a pt-BR,
bindings Python oficiais, funciona em CPU sem GPU. Comparado a alternativas
baseadas em Whisper (`faster-whisper`, `whisper.cpp`): estas puxam `torch`
ou toolchains C++ pesadas como dependência transitiva — exatamente o tipo
de "infraestrutura enorme" que o v0.7 pediu para evitar. Vosk é uma
biblioteca leve (a única coisa "grande" é o modelo, baixado à parte, nunca
automaticamente — ver abaixo).

**TTS: SAPI5 do Windows, via [`pyttsx3`](https://github.com/nateshmbhat/pyttsx3)**
(`services/sapi_tts_provider.py`). SAPI5 já vem instalado no Windows —
**nenhum modelo é baixado** para TTS. `pyttsx3` é só uma camada Python fina
sobre ele (MPL-2.0, mantido, wheels simples). A voz é escolhida
automaticamente: se o Windows tiver uma voz pt-BR instalada, ela é usada
(neste ambiente de desenvolvimento, foi detectada e testada de verdade a
voz `Microsoft Maria` — pt-BR nativa do Windows); senão, cai para a voz
padrão do sistema. Nada de clonagem de voz nem imitação de personagem —
só a síntese padrão do SAPI5.

**Microfone: [`sounddevice`](https://python-sounddevice.readthedocs.io/)**
(MIT, wraps PortAudio, wheel do Windows já traz o binário). Usado só dentro
de `VoskSTTProvider` — nenhum outro módulo importa `sounddevice`.

### Modelo do Vosk — nunca baixado automaticamente

O JARVIS **não baixa nenhum modelo sozinho**. `services/stt_service.py`
procura um modelo em `settings.stt_model_path` (por padrão
`voice_models/vosk-model-small-pt/`, fora do Git — ver `.gitignore`); se não
encontrar, o STT fica `UNAVAILABLE` e o botão de microfone do HUD aparece
desabilitado, sem quebrar nada. Para habilitar STT de verdade:

```powershell
# ~50 MB, licença Apache 2.0, hospedado por alphacephei.com (mantenedores do Vosk)
Invoke-WebRequest -Uri "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip" -OutFile "vosk-model-small-pt.zip"
Expand-Archive -Path "vosk-model-small-pt.zip" -DestinationPath "voice_models"
Rename-Item "voice_models\vosk-model-small-pt-0.3" "vosk-model-small-pt"
Remove-Item "vosk-model-small-pt.zip"
```

(Ou aponte `JARVIS_STT_MODEL_PATH` para qualquer outro modelo Vosk já
baixado.) Depois disso, reinicie o HUD — o botão de microfone liga sozinho
assim que `voice_available` fica `true`.

### Push-to-talk: clique, não pressionar-e-segurar

Considerei as duas alternativas pedidas: pressionar-e-segurar
(`onPressed`/`onReleased` no `MicButton`) vs. clique para começar/clique
para parar. **Escolhi clique-para-alternar** — é a opção mais estável em
Qt/QML: pressionar-e-segurar tem casos de borda reais (soltar o mouse fora
do botão, perder o evento `onReleased` se a janela perder foco no meio do
gesto, Alt+Tab durante o "segurar") que podem deixar o microfone "preso"
ligado sem o usuário perceber — inaceitável dado o requisito de privacidade
do microfone. Clique-para-alternar não tem esse risco (dois eventos
discretos e independentes) e funciona identicamente pelo mouse ou pelo
atalho `Ctrl+Space` (`Shortcut` do Qt dispara só no *press*, então também
favorece um toggle sobre um "segurar").

`Ctrl+Space` só funciona com a janela do JARVIS em foco — é um `Shortcut`
do Qt, escopado à janela, não um hotkey global do Windows (nada escuta
teclado fora do HUD, conforme pedido).

### Privacidade do microfone

- O HUD **nunca** liga o microfone sozinho — só em resposta a um clique (ou
  `Ctrl+Space`) do usuário.
- `MicButton.qml` mostra visualmente os três estados possíveis (parado,
  gravando — violeta pulsando —, transcrevendo); o núcleo central também
  muda de cor (violeta) durante `LISTENING`, então é impossível não notar.
- **Nenhum áudio é salvo em disco, nem temporariamente.** `VoskSTTProvider`
  processa os frames PCM em streaming, direto da callback do PortAudio para
  o reconhecedor do Vosk (`KaldiRecognizer.AcceptWaveform`) — nada é escrito
  em arquivo, nem mesmo um `.wav` temporário. Ver `services/vosk_stt_provider.py`.
- Cancelar (`cancel_listening`) descarta o áudio sem transcrever.

### Transcrição → campo de texto (nunca envio automático)

Quando a transcrição termina, o texto reconhecido cai em
`InputBar.insertTranscription()` — preenche (ou complementa, se já havia
algo digitado) o campo de entrada, para o usuário conferir/editar antes de
enviar. **Nada é enviado à IA automaticamente.** Uma futura opção de
auto-send está deliberadamente fora de escopo aqui — não existe nem
desligada, para não sugerir um comportamento que ainda não foi decidido.

### Fala automática da resposta (`voice_output_enabled`)

Desligada por padrão (`Settings.voice_output_enabled = False`, ou
`JARVIS_VOICE_OUTPUT=1` para ligar por padrão). O controle "OUTPUT ON/OFF"
no `StatusPanel` liga/desliga em runtime
(`JarvisApplication.set_voice_output_enabled()`). Quando ligada, toda
resposta bem-sucedida (`response.completed`) é falada — hoje isso inclui
tanto uma resposta real do Claude (quando configurado) quanto o texto
padrão do fallback ("O serviço de inteligência ainda não está
configurado...", já que ele também é uma "resposta" da perspectiva da
Application Layer). Nenhum traceback ou mensagem técnica é falado — os
textos que chegam ao TTS são sempre os mesmos que já aparecem no chat.

Durante `SPEAKING`, o botão de enviar do `InputBar` vira `■` (mesmo padrão
visual do cancelamento de chat) e chama `JarvisApplication.stop_speaking()`
de verdade — testado ponta a ponta neste ambiente (ver "Validação manual"
no relatório da versão): interromper no meio de uma fala longa corta o
áudio em menos de 1 segundo.

### Nível de voz (waveform)

`VoskSTTProvider` calcula um RMS simples (`array` da stdlib — não `numpy`
nem `audioop`, removido no Python 3.13+) a cada callback do PortAudio
(blocos de 50ms → ~20 updates/s, dentro do orçamento de "algumas dezenas
por segundo" pedido) e entrega isso via `voice.level` no EventBus. O
`Waveform.qml` (barras reagindo ao nível) e o próprio `JarvisCore` (halo
pulsando com o nível) só aparecem/reagem durante `LISTENING`, com dado
real — nunca simulam atividade sem áudio de verdade (por isso não há
"waveform" durante `SPEAKING`: o SAPI5 não expõe amplitude real da fala).

### Eventos

`voice.listening.started/.stopped`, `voice.transcription.started/.completed/.failed`,
`voice.speaking.started/.stopped/.failed`, `voice.level` — todos emitidos
por `VoiceService` no `EventBus` interno e relayados como `AppEvent` por
`JarvisApplication`, exatamente como `state.changed`/`ai.connected` já
funcionavam. O Bridge nunca faz polling para nenhum deles.

### Erros

`AppErrorCode.MICROPHONE_UNAVAILABLE`, `STT_NOT_READY`, `TTS_UNAVAILABLE`,
`VOICE_CANCELLED` — sempre mensagens amigáveis (nunca traceback), exibidas
no HUD pelo mesmo mecanismo já usado para `JARVIS_BUSY` (`busyHint` em
`Main.qml`, agora também escutando `voiceErrorRaised`).

### Voz e permissões — princípio de segurança

Voz **nunca** dá autoridade adicional. Uma frase transcrita é só texto —
segue exatamente o mesmo caminho de qualquer mensagem digitada
(`JarvisApplication.send_message()`), e nenhuma tool real existe ainda para
executar. Quando tools reais existirem, o fluxo continua sendo
`voz → intenção → tool → PermissionService → confirmação do usuário` — a
mesma fundação de permissões do v0.4, não substituída nem contornada pela
voz (ver `docs/architecture.md`).

## Modo de desenvolvimento (`devMode`)

Quando `JARVIS_DEV=1` está no ambiente (mesma flag que já existia em
`config/settings.py`), atalhos ficam disponíveis **só nesse modo**, nunca
para o usuário final:

| Atalho | Efeito |
|---|---|
| `Ctrl+Shift+1` | Simula estado `IDLE` |
| `Ctrl+Shift+2` | Simula estado `THINKING` |
| `Ctrl+Shift+3` | Simula estado `ERROR` |
| `Ctrl+Shift+4` | Dispara um pedido de permissão de teste |
| `Ctrl+Shift+5` | Simula estado `WAITING_CONFIRMATION` |
| `Ctrl+Shift+6` | Simula estado `LISTENING` (com um `voiceLevel` fixo de teste) |
| `Ctrl+Shift+7` | Simula estado `SPEAKING` |
| `Ctrl+Shift+8` | Simula estado `PROCESSING_SPEECH` (v0.8) |

Nenhuma resposta de IA é simulada, e nenhuma transcrição/fala falsa é
gerada — só estados visuais e o overlay de permissão, para poder validar a
interface sem depender de uma sessão real nem de microfone/TTS reais.

**Overlay técnico (v0.8, opcional):** também só em `devMode`, um pequeno
texto no canto superior direito mostra `STATE`/`BUSY`/`VOICE LEVEL` em
tempo real — só para acelerar desenvolvimento visual, sem precisar
instrumentar QML toda vez. Nunca aparece fora de `devMode`
(`tests/test_qml_smoke.py::test_devmode_simulated_states_never_apply_when_devmode_off`
cobre isso, junto com o resto do dev mode).

`ChatPanel.qml` também ganhou uma `property bool pending` (ligada a
`bridge.busy` em `Main.qml`): enquanto uma resposta está em andamento,
mostra três pontos discretos pulsando no rodapé da lista — atividade real
do backend, não um spinner genérico nem texto de resposta inventado.

## Testes

- `tests/test_bridge.py` — offline, com `JarvisApplication` real sobre
  `FakeAIService` (mesmos fakes usados pelo backend). Sem GUI, sem QML.
  Inclui `test_dev_mode_defaults_to_false`.
- `tests/test_message_model.py` — `MessageListModel` isolado: roles,
  `sync()` incremental/reset, `update_content()`.
- `tests/test_qml_smoke.py` — confirma que `Main.qml` carrega sem
  erros/warnings, com `QT_QPA_PLATFORM=offscreen`. Inclui dois testes
  específicos do `PermissionOverlay` (localizado via `objectName:
  "permissionOverlay"`): oculto sem pedido pendente, visível e com os dados
  certos quando existe um. **Não** testa pixels — isso é responsabilidade de
  inspeção visual manual.
- `tests/test_voice_service.py` (novo no v0.7) — `VoiceService` isolado,
  com `FakeSTTService`/`FakeTTSService` (`tests/fakes.py`): disponibilidade,
  push-to-talk, transcrição (sucesso/falha), fala (sucesso/falha),
  cancelamento (inclusive interrompendo uma fala em andamento), shutdown.
  Nenhum microfone ou engine de voz real é tocado.
- `tests/test_application.py` (classes `ApplicationVoice*`) — a mesma
  cobertura, mas pela API pública de `JarvisApplication`: estados
  `LISTENING`/`PROCESSING_SPEECH`/`SPEAKING`, transcrição chegando como
  `AppEvent` (nunca enviada à IA sozinha), fala automática condicionada a
  `voice_output_enabled`, guardas de concorrência com o chat, ausência de
  microfone não quebrando o chat normal, shutdown limpo durante
  listening/speaking.
- **Novo no v0.8** — `tests/test_qml_smoke.py`: `Core` reage a todos os
  estados simulados em dev mode (`test_core_reflects_devmode_simulated_states`),
  LISTENING visualmente distinto de THINKING
  (`test_core_listening_is_visually_distinct_from_thinking`), dev mode não
  vaza nenhum estado/permissão fake quando desligado
  (`test_devmode_simulated_states_never_apply_when_devmode_off`), e a janela
  redimensiona por 1100×700 até 2560×1440 sem gerar warning
  (`test_window_resizes_across_target_resolutions_without_warnings`).
- **Novo no v0.8** — `tests/test_bridge.py`: `BridgeVoiceTests` (push-to-talk
  e fala pelos slots reais do Bridge — `toggleListening`/`stopSpeaking` —
  com `FakeSTTService`/`FakeTTSService`, não só pela Application Layer
  diretamente) e `BridgeStreamingPrepTests` (simula `response.started` →
  múltiplos `response.delta` → confirma que `MessageListModel.update_content()`
  atualiza a mesma linha progressivamente, sem criar mensagens novas —
  preparação para streaming real, sem Claude conectado).

## Limitações desta versão

- STT exige baixar manualmente o modelo Vosk (ver seção "Voz" acima) — sem
  ele, o botão de microfone fica desabilitado, e a tooltip diz exatamente
  por quê (mic ausente vs. modelo ausente são mensagens diferentes).
- Sem wake word / escuta permanente (só push-to-talk, de propósito nesta
  etapa) e sem hotkey global (`Ctrl+Space` só funciona com a janela em foco).
- A fala automática (`voice_output_enabled`) já funciona de ponta a ponta
  (testada com um engine SAPI5 real neste ambiente), mas como o Claude real
  ainda não está ativado, o que é falado hoje é sempre o texto de
  fallback/erro amigável, nunca uma resposta inteligente de verdade.
- Sem streaming real de texto — o Bridge já sabe reagir a um `response.delta`
  (testado com eventos fake), mas nenhum backend o emite ainda.
- Sem MCP, sem ferramentas reais, sem Ruflo — tudo isso continua planejado,
  não implementado.
- Sem cantos de janela arredondados (decisão deliberada — ver "Layout,
  boot, resize, fullscreen" acima), sem tela de configurações, sem temas
  alternativos, sem persistência de janela (posição/tamanho não são
  lembrados entre execuções), sem seleção de dispositivo de microfone/voz
  na UI (usa sempre o padrão do sistema — a estrutura já suporta trocar
  isso depois, ver `Settings.tts_voice`).
- Validação visual "de verdade" (like, olhar para a tela e avaliar
  acabamento, ou realmente falar no microfone) precisa ser feita por quem
  está rodando — os testes automatizados cobrem comportamento e ausência de
  erros com fakes, não hardware real nem estética. TTS foi validado
  ponta-a-ponta neste ambiente (síntese real + interrupção real); STT não
  (nenhum modelo foi baixado, por instrução).

## Próximas versões (direção, não implementado)

Registrado aqui só como direção arquitetural — nada disto está implementado
e a numeração ainda pode mudar:

- **Claude real / API / streaming** — ativar `ANTHROPIC_API_KEY`, validar
  `ClaudeAgentProvider` com uma sessão de verdade, ligar `response.delta`.
- **Tools + Permissions** — primeiras ferramentas reais (provavelmente READ
  primeiro), conectadas ao `PermissionOverlay` que já existe.
- **MCP** — servidores MCP como integrações externas.
- **Ruflo / multiagentes** — orquestração multiagente para tarefas
  complexas, opcional, não obrigatória.
- **Voz avançada** — wake word, seleção de dispositivo/voz na UI,
  automações disparadas por voz (sempre atrás de `PermissionService`).
