# frontend/

O HUD do JARVIS — interface gráfica real do projeto, introduzida no v0.5,
refinada visualmente no v0.6 (HUD Refinement / UX Foundation), com voz desde
o v0.7 (Voice Foundation), um segundo refinamento visual profundo no v0.8
(HUD Overhaul / UX 2.0), e **contas locais + chats persistidos + microfone
corrigido no v0.9 (Accounts, Persistent Chats & Voice Input Fix)**. PySide6
(Qt for Python) + Qt Quick/QML. O terminal (`python main.py`,
`app/terminal.py`) continua existindo, separado e inalterado — este é um
segundo frontend, não uma substituição, e não ganhou contas nem voz (ambos
são recursos específicos do HUD).

**v0.9 muda a arquitetura de entrada do HUD**: antes de qualquer coisa, é
preciso estar logado (`AuthScreen`) — só depois o HUD de sempre aparece,
agora com uma sidebar de chats. O design system (`Theme.qml`), o núcleo de
IA e o resto do HUD v0.8 continuam exatamente iguais. Ver "Contas (v0.9)" e
"Voz (v0.9)" abaixo para o detalhe.

**v1.0 acrescenta ao HUD**: campo de e-mail no cadastro, overlay de
verificação de e-mail (`EmailVerificationOverlay.qml`) com contadores
derivados de timestamps reais do backend, estado de e-mail no painel de
conta, e o indicador de IA agora mostrando o provider real (`OPENROUTER
(FREE)` quando `free_only` está ligado — o mesmo `bridge.aiBackend` de
sempre, agora com conteúdo de verdade). Também corrige o overflow de texto
do `VoiceSetupOverlay` (ver "Correções visuais da v1.0").

## Como executar

```powershell
pip install -r requirements.txt
python -m frontend
```

Na primeira execução, o HUD mostra a tela de login/criação de conta — é
preciso criar uma conta local (usuário/senha) antes de usar o resto do HUD
(ver "Contas" abaixo). Sem `ANTHROPIC_API_KEY` configurada, o HUD abre
normalmente e mostra `AI OFFLINE` — não trava, não pede credencial, não
finge estar conectado. `requirements.txt` já inclui as dependências de voz
(`vosk`, `sounddevice`, `pyttsx3`); a fala (TTS) funciona assim que elas
estão instaladas, mas o reconhecimento de fala (STT) exige instalar o
modelo Vosk — agora **de dentro do próprio HUD**, clicando no microfone (ver
seção "Voz" abaixo).

## Arquitetura

```
JARVIS HUD (PySide6 / QML)
        │
        ↓
Frontend Bridge (frontend/bridge.py)
        │
        ↓
AccountManager (app/account_manager.py) — contas, sessão, chats, memória por usuário
        │
        ↓
JarvisApplication (app/application.py)  — só existe enquanto alguém está logado
        │              \
        ↓                → VoiceService (services/voice_service.py)
JarvisCore → Orchestrator      ├→ SpeechToTextService (STT)
        │                      └→ TextToSpeechService (TTS)
        ↓
AIService / MemoryService / Services
```

O QML **nunca** importa nada de `services/`, `app/core.py`,
`app/application.py`, `app/account_manager.py`, o Claude Agent SDK, `vosk`,
`sounddevice`, `pyttsx3` ou SQLite — só conhece `bridge`, exposto como
propriedade de contexto do QML
(`engine.rootContext().setContextProperty("bridge", bridge)` em
`frontend/launcher.py`). `VoiceService` também não conhece Qt: fala só com
`SpeechToTextService`/`TextToSpeechService` (abstrações) e emite no mesmo
`EventBus` interno que `JarvisCore` já usava desde o v0.3 — quem traduz
tudo isso para QML continua sendo só o Bridge.

### `frontend/bridge.py` — `JarvisBridge`

Ponte fina entre QML e `AccountManager`/`JarvisApplication`: sem lógica de
domínio, só tradução. Expõe Properties Qt de sempre (`jarvisState`,
`running`, `busy`, `memoryAvailable`, `aiConfigured`, `aiBackend`,
`aiSessionActive`, `pendingPermission`, `messages`, `devMode`, `canClose`,
`voiceAvailable`, `ttsReady`, `voiceOutputEnabled`, `voiceLevel`) mais as de
conta (v0.9): `authenticated`, `currentUser`, `conversations`,
`currentConversationId`, `sttStatus`, `voiceModelInstalled`,
`voiceModelDownloadActive`, `voiceModelDownloadProgress`, `voiceModelInfo`.
Slots de sempre (`sendMessage`, `cancelCurrentRequest`, `approvePermission`,
`denyPermission`, `requestShutdown`, `simulateState` — dev only —,
`toggleListening`, `cancelListening`, `stopSpeaking`,
`setVoiceOutputEnabled`) mais os de conta/chat/voz (v0.9): `register`,
`login`, `logout`, `startNewConversation`, `openConversation`,
`searchConversations`, `renameConversation`, `deleteConversation`,
`downloadVoiceModel`, `cancelVoiceModelDownload`.

**`self._app` é uma property (v0.9), não uma referência fixa** — sempre lê
`self._account.app`, que só existe entre um login e o logout/shutdown
seguinte. Todo slot que precisa da Application Layer checa
`self._app is not None` antes de agir.

**Nunca faz polling.** `bridge.initialize()` (chamado uma vez, no início do
processo) tenta um auto-login a partir da sessão local persistida; a cada
sessão aberta (auto-login ou login manual), `_enter_session()` chama
`JarvisApplication.subscribe()` (síncrono — a fila é registrada
imediatamente, sem depender de uma task ainda rodar) e consome essa fila em
uma única task de fundo por sessão (`_consume_events`). Cada evento
relevante dispara uma releitura pontual de `get_status()`/`get_messages()`/
lista de conversas — nunca em loop/timer.

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
├── Main.qml                 janela, layout, boot, resize, shortcuts, AuthScreen/hudRow
├── theme/
│   ├── Theme.qml             singleton — paleta, spacing, durações, tipografia
│   └── qmldir
└── components/
    ├── TitleBar.qml           barra de título própria (move/resize/min/max/close via API nativa do Qt)
    ├── WindowButton.qml       botão de controle de janela
    ├── JarvisCore.qml         núcleo animado — o elemento visual principal
    ├── AuthScreen.qml         tela de login/criação de conta — v0.9
    ├── AuthField.qml          campo de texto rotulado (usuário/senha) — v0.9
    ├── Sidebar.qml            barra lateral retrátil: chats, busca, conta — v0.9
    ├── SidebarIconButton.qml  botão de ícone (toggle de recolher/expandir) — v0.9
    ├── SidebarConversationRow.qml  uma linha de conversa na sidebar — v0.9
    ├── AccountPanel.qml       modal de conta (nome, e-mail/verificação, plano, sair) — v0.9/v1.0
    ├── VoiceSetupOverlay.qml  modal de instalação do modelo de voz — v0.9
    ├── EmailVerificationOverlay.qml  verificação de e-mail com contadores reais — v1.0
    ├── ChatPanel.qml          lista de conversa, scroll inteligente, indicador de resposta pendente
    ├── MessageItem.qml        uma mensagem (bloco discreto, não bubble)
    ├── InputBar.qml           entrada multiline + microfone: [texto][MIC][SEND]
    ├── MicButton.qml          botão de microfone — 5 estados (SETUP_REQUIRED/READY/LISTENING/PROCESSING/ERROR) desde o v0.9
    ├── Waveform.qml           nível de voz real durante LISTENING — v0.7
    ├── StatusPanel.qml        faixa de status real (CORE/MEMORY/AI/SESSION/VOICE + OUTPUT ON/OFF)
    ├── StatusIndicator.qml    ponto + label reutilizável
    ├── ActionButton.qml       botão genérico reutilizável (com tooltip embutido)
    └── PermissionOverlay.qml  pedido de permissão (READ/ACTION/DANGEROUS)
```

## Contas (v0.9)

**Tela de entrada** (`AuthScreen.qml`): antes de qualquer sessão local
válida, `Main.qml` mostra só isso — HUD/sidebar ficam com `visible: false`
(`hudRow`), não só escondidos atrás de um overlay. Segue o mockup pedido
(JARVIS + "PERSONAL INTELLIGENCE SYSTEM" + ENTRAR/CRIAR CONTA), com o mesmo
`JarvisCore` em miniatura para consistência visual — não é um formulário web
genérico. Campos mínimos para criar conta: nome de exibição, usuário, senha
— sem e-mail (nenhuma função real dependeria disso ainda). `AuthField.qml` é
um campo rotulado reutilizado nos dois formulários (entrar/criar).

**Sessão persistida**: depois de logar, fechar e reabrir o HUD continua
logado (token local cifrado via DPAPI — ver `docs/architecture.md`, seção
"Contas locais") até um logout explícito.

**Sidebar** (`Sidebar.qml`): retrátil (~264px expandida, ~60px colapsada,
animada), com "+ Novo chat", busca local, conversas agrupadas por data
(Hoje/Ontem/Últimos 7 dias/Mais antigos — `_recomputeGroups()`, recalculado
a cada mudança na lista), e o chip de conta/plano no rodapé (avatar com
inicial, nome, plano). Substitui o antigo botão "NOVA CONVERSA" solto
abaixo do Core. Quando colapsada, mostra só ícones — o Core reganha o
espaço horizontal. Clicar no chip de conta abre `AccountPanel.qml` (modal
simples: nome, `@usuario`, plano, SAIR — não uma tela de configurações
inteira).

**Isolamento**: a sidebar/chat só mostra o que `bridge.conversations`
devolve, que já vem filtrado por usuário do lado do Python
(`AccountManager.list_conversations()`) — o QML nunca decide isolamento,
só exibe o que já chegou filtrado.

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

## Verificação de e-mail (v1.0)

`EmailVerificationOverlay.qml`. Segue o mockup do escopo: e-mail mascarado
(`d***@example.com` — a tela não precisa do endereço inteiro), campo de 6
dígitos, contador de expiração (`MM:SS`) e contador de reenvio.

**Os dois contadores não são timers de UI.** Eles vêm de
`bridge.verificationSecondsUntilExpiry`/`...UntilResend`, que o Bridge
calcula a partir dos timestamps persistidos no banco
(`expires_at`/`resend_available_at`). O `Timer` do QML só decrementa a
exibição entre atualizações — fechar e reabrir o JARVIS mostra o tempo real
restante, nunca um contador reiniciado. Toda a validação (expirou? pode
reenviar?) acontece no backend; o frontend nunca é autoridade sobre isso.

Sem SMTP configurado, o overlay diz claramente que o envio não está
configurado e que a conta continua funcionando — nunca finge ter enviado.

## Chat: Markdown e ações (v1.2)

**Markdown renderizado.** O chat mostra Markdown de verdade (headings,
negrito/itálico, listas, código inline e em bloco, blockquote, regras
horizontais, tabelas) via `TextEdit.MarkdownText` — o Qt renderiza
nativamente. Antes a marcação aparecia crua (`### Título`, `**negrito**`).

**O texto RAW nunca muda.** O modelo expõe dois papéis:
`content` (RAW, exatamente como está no banco — é o que o Copy entrega) e
`markdown` (o mesmo texto sanitizado, só para exibição). Nada da
sanitização é persistido.

**Segurança.** O renderizador de Rich Text do Qt também interpreta HTML
embutido, e o texto vem de uma IA — então tudo passa por
`services/markdown_safety.py` antes: tags viram texto literal
(`&lt;script&gt;`), e `javascript:`/`vbscript:`/`data:`/`file:` são
quebrados. **Escapamos em vez de remover**, para o usuário continuar vendo
o que a IA escreveu (importante quando se pede "me mostre um exemplo de
HTML"). Blocos de código ficam intactos — lá dentro, HTML é conteúdo que se
quer ler. Links são renderizados com estilo mas **não são clicáveis**:
abrir URL vinda da IA com um clique seria um vetor de phishing.

**Ações por mensagem.** Abaixo de cada mensagem, controles discretos que
ganham presença no hover:

| Ação | Onde | O que faz |
|---|---|---|
| Copiar | todas | Copia o RAW — nunca "YOU"/"JARVIS", horário ou markup renderizado. Mostra ✓ por ~1,6s. |
| Regenerar | só respostas do JARVIS | Gera outra resposta para a **mesma** pergunta. |

**Regenerate** acha a mensagem de usuário que originou aquela resposta
específica (procurando para trás a partir da posição dela), então funciona
em respostas do meio do histórico — não só na última. A resposta antiga é
substituída *no lugar*, preservando id, papel e posição: o prompt não
duplica, o histórico não embaralha, e o banco reescreve a mesma linha.

## Horário local (v1.2)

O horário das mensagens era exibido em UTC — uma mensagem enviada às 21:11
em UTC-3 aparecia como 00:11. A persistência já estava certa (UTC com
offset, que é o correto para um instante absoluto); o erro estava só na
ponta, que formatava o UTC direto. Agora a conversão usa `astimezone()`,
sem offset fixo nem fuso hardcoded — continua correto em outro país ou
depois de mudança de horário de verão.

## Correções visuais da v1.2

**Modais tortos/cortados** — os quatro overlays (e-mail, voz, conta,
permissão) usavam `Row { layoutDirection: RightToLeft }` para os botões. Um
`Row` não quebra linha nem encolhe filhos: medindo o overlay de e-mail, a
linha tinha `width=364` para `childrenRect.width=616`, com dois botões em
**x negativo** (-70 e -252) desenhados fora do cartão. Era isso que fazia o
modal parecer deslocado com a borda cortada.

Corrigido na causa, com componente compartilhado:

- `ModalButtonRow.qml` (um `Flow`) — mesmo alinhamento à direita, mas
  quebra para a linha de baixo quando não cabe;
- `ActionButton` ganhou teto de largura (`Math.min(natural, parent.width)`)
  e elide no rótulo — um `Flow` não resolve um item **sozinho** maior que a
  linha, que era o caso de "BAIXAR MODELO DE VOZ (~45 MB)" (436px em 384px).

Sem offsets mágicos. `tests/test_overlay_layout.py` mede geometria real em
6 resoluções e falha se alguém reintroduzir o padrão.

## Correções visuais da v1.0

**Overflow do `VoiceSetupOverlay`** — as linhas rótulo/valor (Idioma,
Tamanho, Licença, **Origem**) eram `Row`s com um espaçador de largura fixa
(`parent.width - 160`). Com a janela estreita, ou com um valor longo como
`alphacephei.com (mantenedores oficiais do Vosk)`, o texto vazava para fora
do cartão. Agora cada linha é um `Item` com rótulo ancorado à esquerda,
valor à direita, e o valor limitado pelo espaço restante — quebra linha em
vez de vazar. O mesmo padrão foi usado na linha de e-mail do `AccountPanel`.

## Voz (v0.9 — setup do modelo e push-to-talk corrigido)

Entrada (STT) e saída (TTS) de voz, ambas offline, ambas opcionais — sem
nenhuma delas instalada/configurada, o JARVIS funciona normalmente por
texto (voz é uma capability, não uma dependência).

**O que estava quebrado (v0.7/v0.8) e o que mudou no v0.9**: o botão de
microfone mostrava só "indisponível" sem dizer por quê — modelo Vosk
ausente, microfone ausente e qualquer outra falha caíam todas na mesma
mensagem genérica. `services/stt_service.py` agora distingue os quatro
casos de verdade (`STTStatus`: `READY`/`SETUP_REQUIRED`/`NO_MICROPHONE`/
`UNAVAILABLE`), e `VoskSTTProvider` deixou de presumir 16 kHz fixo (que
podia quebrar/degradar a captura em microfones com outro sample rate
nativo) — agora detecta a taxa real do dispositivo e reamostra em software.
Ver `docs/architecture.md`, seção "Diagnóstico e correção do microfone",
para o relato completo.

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

### Modelo do Vosk — instalável de dentro do próprio HUD (v0.9)

O JARVIS **não baixa nenhum modelo sozinho**. `services/stt_service.py`
procura um modelo em `settings.stt_model_path` (por padrão
`data/models/vosk/vosk-model-small-pt/`, fora do Git — ver `.gitignore`,
movido de `voice_models/` no v0.9); se não encontrar, `MicButton` mostra o
estado `SETUP_REQUIRED` (badge âmbar, tooltip "Configurar reconhecimento de
voz"). Passo a passo real, pelo próprio HUD:

1. Abra o HUD, logado em uma conta.
2. Clique no botão de microfone (badge âmbar, ao lado do campo de texto).
3. O `VoiceSetupOverlay` abre, mostrando idioma (Português - Brasil),
   tamanho aproximado (~45 MB), licença (Apache 2.0) e origem
   (`alphacephei.com`, mantenedores oficiais do Vosk) — nada é baixado
   ainda.
4. Clique em "BAIXAR MODELO DE VOZ (~45 MB)". Uma barra de progresso real
   aparece (bytes baixados / total, via `Content-Length` do servidor);
   "CANCELAR" interrompe a qualquer momento sem deixar arquivo parcial
   (`VoiceModelManager`, ver `docs/architecture.md`).
5. Ao terminar, o overlay fecha sozinho e o microfone já fica `READY` — sem
   precisar reiniciar o HUD (`downloadVoiceModel()` troca o provider de STT
   da sessão atual na hora).

(Ou aponte `JARVIS_STT_MODEL_PATH` para qualquer outro modelo Vosk já
baixado manualmente, se preferir não usar o instalador do HUD.)

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
- `MicButton.qml` mostra visualmente os 5 estados possíveis (v0.9:
  `SETUP_REQUIRED`/`READY`/`LISTENING`/`PROCESSING`/`ERROR`, cada um com
  tooltip própria); o núcleo central também muda de cor (violeta) durante
  `LISTENING`, então é impossível não notar.
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

- `tests/test_account_manager_auth.py` (v0.9) — 10 cenários de conta contra
  `AccountManager` real + SQLite temporário: criar conta, username
  duplicado, senha errada, login correto, logout, sessão persistida entre
  "execuções", sessão inválida, senha nunca em texto puro (lê a coluna do
  banco direto), token de uma conta não autentica como outra, sessão
  expirada é rejeitada e removida.
- `tests/test_account_manager_conversations.py` (v0.9) — 11 cenários de
  chat persistido: criar conversa, mensagens salvas via evento
  (`message.received`/`response.completed`), sobrevive a um restart da
  Application Layer, persiste entre logout/login, carregar conversa antiga,
  listar, ordenar (mais recente primeiro), buscar, renomear, excluir,
  isolamento entre usuários.
- `tests/test_memory_migration_and_isolation.py` (v0.9) — migração da
  memória legacy (fixtures temporárias, nunca a memória real do projeto —
  com um teste dedicado que verifica byte-a-byte que `memory/profile.md`
  real não foi tocado), não sobrescreve memória já existente, memória
  isolada por conta.
- `tests/test_entitlements_and_voice_model_manager.py` (v0.9) — FREE/PRO
  sem billing, e `VoiceModelManager` inteiro sem rede real: download com
  `urllib.request.urlopen` substituído por um fake local, progresso,
  cancelamento no meio do download, erro de rede, e Zip Slip bloqueado com
  um `.zip` malicioso construído localmente (nunca baixado).
- `tests/test_bridge.py` — offline, com `AccountManager`/`JarvisApplication`
  reais sobre `FakeAIService` (mesmos fakes usados pelo backend). Sem GUI,
  sem QML. `BridgeAccountTests` cobre registro/login/logout pelos slots
  reais do Bridge, incluindo `authErrorRaised` em username duplicado e
  senha errada. Inclui `test_dev_mode_defaults_to_false`.
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
- `tests/test_bridge.py`: `BridgeVoiceTests` (push-to-talk e fala pelos
  slots reais do Bridge — `toggleListening`/`stopSpeaking` — com
  `FakeSTTService`/`FakeTTSService`, incluindo o novo `sttStatus`
  `"setup_required"`/`"no_microphone"`) e `BridgeStreamingPrepTests` (simula
  `response.started` → múltiplos `response.delta` → confirma que
  `MessageListModel.update_content()` atualiza a mesma linha
  progressivamente, sem criar mensagens novas — preparação para streaming
  real, sem Claude conectado).
- `tests/test_qml_smoke.py` (v0.9) — cobre os dois estados do HUD:
  `AuthScreen` visível/`hudRow` escondido antes do login, os dois trocando
  de lugar reativamente após um registro real (`bridge._register(...)`),
  sidebar refletindo o usuário logado, toggle de colapsar/expandir sem
  warning, logout voltando para a tela de login, e resize (1100×700 até
  2560×1440) tanto com a sidebar expandida quanto colapsada — sempre zero
  warnings.

## Limitações desta versão

- Sem verificação de e-mail nem recuperação de conta (contas são só
  usuário/senha local nesta versão).
- Sem sincronização de contas/chats entre computadores (tudo é local a esta
  máquina — `data/jarvis.db`).
- Sem wake word / escuta permanente (só push-to-talk, de propósito nesta
  etapa) e sem hotkey global (`Ctrl+Space` só funciona com a janela em foco).
- A fala automática (`voice_output_enabled`) já funciona de ponta a ponta
  (testada com um engine SAPI5 real neste ambiente), mas como o Claude real
  ainda não está ativado, o que é falado hoje é sempre o texto de
  fallback/erro amigável, nunca uma resposta inteligente de verdade.
- Sem streaming real de texto — o Bridge já sabe reagir a um `response.delta`
  (testado com eventos fake), mas nenhum backend o emite ainda.
- Sem MCP, sem ferramentas reais, sem Ruflo, sem billing real — tudo isso
  continua planejado, não implementado.
- Busca de chat usa `LIKE` simples (sem FTS) — caracteres `%`/`_` na busca
  do usuário funcionam como curinga do SQLite (comportamento do `LIKE`, não
  uma falha de segurança: a busca continua escopada ao usuário logado).
- Sem cantos de janela arredondados (decisão deliberada — ver "Layout,
  boot, resize, fullscreen" acima), sem tela de configurações, sem temas
  alternativos, sem persistência de janela (posição/tamanho não são
  lembrados entre execuções), sem seleção de dispositivo de microfone/voz
  na UI (usa sempre o padrão do sistema — a estrutura já suporta trocar
  isso depois, ver `Settings.tts_voice`).
- Validação visual "de verdade" (olhar para a tela e avaliar acabamento, ou
  realmente falar no microfone depois de instalar o modelo) precisa ser
  feita por quem está rodando — os testes automatizados cobrem
  comportamento e ausência de erros com fakes, não hardware real nem
  estética.

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
- **Contas na nuvem** — sincronização de chats/conta entre computadores,
  verificação de e-mail, billing real do plano PRO. Nada disso existe hoje;
  a base local-first do v0.9 não impede, mas também não implica nenhuma
  dessas peças.
