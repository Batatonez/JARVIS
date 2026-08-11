# frontend/

O HUD do JARVIS — interface gráfica real do projeto, introduzida no v0.5 e
refinada visualmente no **v0.6 (HUD Refinement / UX Foundation)**. PySide6
(Qt for Python) + Qt Quick/QML. O terminal (`python main.py`,
`app/terminal.py`) continua existindo, separado e inalterado — este é um
segundo frontend, não uma substituição.

**v0.6 não muda a arquitetura do v0.5** (HUD → Bridge → JarvisApplication
continua igual) — é refinamento visual/UX sobre a mesma base: contraste,
núcleo v2, layout, boot em etapas, e a correção de um bug real do
`PermissionOverlay` (ver seção "Permissões" abaixo).

## Como executar

```powershell
pip install -r requirements.txt
python -m frontend
```

Sem `ANTHROPIC_API_KEY` configurada, o HUD abre normalmente e mostra
`AI OFFLINE` — não trava, não pede credencial, não finge estar conectado.

## Arquitetura

```
JARVIS HUD (PySide6 / QML)
        │
        ↓
Frontend Bridge (frontend/bridge.py)
        │
        ↓
JarvisApplication (app/application.py)
        │
        ↓
JarvisCore → Orchestrator → AIService / MemoryService / Services
```

O QML **nunca** importa nada de `services/`, `app/core.py`,
`app/application.py` ou o Claude Agent SDK — só conhece `bridge`, exposto
como propriedade de contexto do QML (`engine.rootContext().setContextProperty("bridge", bridge)`
em `frontend/launcher.py`).

### `frontend/bridge.py` — `JarvisBridge`

Ponte fina entre QML e `JarvisApplication`: sem lógica de domínio, só
tradução. Expõe Properties Qt (`jarvisState`, `running`, `busy`,
`memoryAvailable`, `aiConfigured`, `aiBackend`, `aiSessionActive`,
`activeConversation`, `pendingPermission`, `messages`, `devMode`,
`canClose`) e Slots (`sendMessage`, `cancelCurrentRequest`,
`newConversation`, `approvePermission`, `denyPermission`,
`requestShutdown`, `simulateState` — este último só ativo com `devMode`).

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
    ├── InputBar.qml           entrada multiline, Enter envia, Shift+Enter quebra linha
    ├── StatusPanel.qml        faixa de status real (CORE/MEMORY/AI/SESSION)
    ├── StatusIndicator.qml    ponto + label reutilizável
    ├── ActionButton.qml       botão genérico reutilizável (com tooltip embutido)
    └── PermissionOverlay.qml  pedido de permissão (READ/ACTION/DANGEROUS)
```

### `Theme.qml`

Nenhuma cor/espaçamento/duração é hardcoded fora daqui — sempre `Theme.*`.
Paleta v0.6 (mais contraste que o v0.5, mesma direção): fundo quase preto
com leve azul (`background #070A10`), superfícies azul-grafite
(`surface`/`surfaceElevated`/`surfacePanel`), ciano frio (`cyan #5CE1E6`)
como primária, azul elétrico (`blue #4C8DFF`) como secundária, violeta
(`violet #9B6BFF`) só como accent pontual (partículas/segmentos). Texto:
`textPrimary` quase branco levemente azulado, `textSecondary`/`textMuted`/
`textFaint` — os três foram clareados em relação ao v0.5, que deixava
status, placeholders e labels pouco legíveis sobre o fundo escuro. Fonte:
`Segoe UI Variable` com fallback automático do próprio Windows para
`Segoe UI`.

### `JarvisCore.qml` — o núcleo (AI Core) v2

O elemento visual central, desenhado inteiramente em QML (Qt Quick Shapes +
`Rectangle`s + animações nativas — **sem** GIF, vídeo ou pintura por frame
em Python). Camadas: glow ambiente (4 círculos concêntricos translúcidos,
sem shader), 3 anéis com arcos incompletos (`PathAngleArc`, cada um com
rotação/velocidade próprias), um **anel segmentado** (16 ticks discretos
girando numa velocidade própria — a camada nova do v0.6, dá profundidade
sem virar bagunça visual), partículas orbitais, e um núcleo central com
pulso de "respiração".

Reage a estado real via três propriedades (`state`, `aiConfigured`,
`aiSessionActive`), vindas do Bridge — `idle | thinking | working |
listening | speaking | waiting_confirmation | error` são os únicos estados
válidos hoje (`app/state.py`), e o componente já sabe reagir a todos eles
mesmo que o backend só produza `idle`/`thinking`/`error` em runtime:
- **IDLE** — respiração suave, rotações lentas independentes.
- **THINKING/WORKING/LISTENING/SPEAKING** — rotação mais rápida, pulso mais
  forte (`alert`).
- **WAITING_CONFIRMATION** — accent âmbar (`Theme.stateColor`), sem acelerar
  a rotação — é espera, não processamento.
- **ERROR** — accent vermelho/coral com transição suave, pulso de alerta
  mais rápido (mesmo `alert` do THINKING) e o anel interno **congela**
  brevemente (`running: !core.errored`) para dar sensação de interrupção —
  sem flash agressivo — e retoma sozinho quando o estado sai de `error`.
- **Sem IA configurada/sessão inativa** — brilho reduzido (`dim = 0.72`,
  era 0.68 no v0.5), mas o núcleo nunca "morre" nem some.

### Boot, resize, fullscreen

Sequência de entrada em etapas, ~1s no total (v0.6: era um único degrau no
v0.5): núcleo aparece primeiro (~120ms), depois a região núcleo+chat
(~320ms), depois a faixa de status (~620ms), depois o input (~760ms) — só
cosmético, nunca bloqueia o backend (que inicia em paralelo, de forma
totalmente independente). `F11` alterna fullscreen, `Esc` sai dele. Janela
sem moldura nativa (`FramelessWindowHint`): mover usa
`Window.startSystemMove()`, redimensionar pelas bordas usa
`Window.startSystemResize(edges)` — APIs oficiais do Qt, sem cálculo manual
de coordenadas.

**Limitação conhecida:** com moldura customizada, o menu de Snap Layouts do
Windows 11 (que aparece ao pairar o mouse sobre o botão nativo de maximizar)
não existe, já que o botão de maximizar é desenhado por nós. Arrastar a
janela até a borda da tela (Aero Snap) continua funcionando normalmente,
porque usa `startSystemMove()` (API real do SO).

## Permissões (fundação, sem ferramentas reais)

`PermissionOverlay.qml` reage a `bridge.pendingPermission` (populado via os
eventos `permission.requested`/`permission.resolved`, já existentes desde
o v0.4). A cor do cartão depende do `riskLevel` (`read` = ciano,
`action` = âmbar, `dangerous` = vermelho). Nenhuma ferramenta real dispara
isso ainda — só existe para o backend/frontend já saberem se comunicar
quando ferramentas reais existirem.

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

Nenhuma resposta de IA é simulada — só estados visuais e o overlay de
permissão, para poder validar a interface sem depender de uma sessão real.

`ChatPanel.qml` também ganhou uma `property bool pending` (ligada a
`bridge.busy` em `Main.qml`): enquanto uma resposta está em andamento,
mostra três pontos discretos pulsando no rodapé da lista — atividade real
do backend, não um spinner genérico nem texto de resposta inventado.

## Testes

- `tests/test_bridge.py` — offline, com `JarvisApplication` real sobre
  `FakeAIService` (mesmos fakes usados pelo backend). Sem GUI, sem QML.
  Inclui `test_dev_mode_defaults_to_false` (v0.6).
- `tests/test_message_model.py` (novo no v0.6) — `MessageListModel` isolado:
  roles, `sync()` incremental/reset, `update_content()`.
- `tests/test_qml_smoke.py` — confirma que `Main.qml` carrega sem
  erros/warnings, com `QT_QPA_PLATFORM=offscreen`. v0.6 adiciona dois testes
  específicos do `PermissionOverlay` (localizado via `objectName:
  "permissionOverlay"`): oculto sem pedido pendente, visível e com os dados
  certos quando existe um. **Não** testa pixels — isso é responsabilidade de
  inspeção visual manual.

## Limitações desta versão

- Sem streaming real (o contrato de eventos já suporta; `MessageListModel.update_content()`
  já existe como ponto de extensão — falta só ligar `response.delta` quando
  o Agent SDK real estiver conectado).
- Sem voz, sem MCP, sem ferramentas reais, sem Ruflo — tudo isso continua
  planejado, não implementado.
- Sem tela de configurações, sem temas alternativos, sem persistência de
  janela (posição/tamanho não são lembrados entre execuções).
- Validação visual "de verdade" (like, olhar para a tela e avaliar
  acabamento) precisa ser feita por quem está rodando — os testes
  automatizados cobrem comportamento e ausência de erros, não estética.
