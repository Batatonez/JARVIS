# frontend/

O HUD do JARVIS — a primeira interface gráfica real do projeto (v0.5).
PySide6 (Qt for Python) + Qt Quick/QML. O terminal (`python main.py`,
`app/terminal.py`) continua existindo, separado e inalterado — este é um
segundo frontend, não uma substituição.

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
    ├── ChatPanel.qml          lista de conversa, scroll inteligente
    ├── MessageItem.qml        uma mensagem (bloco discreto, não bubble)
    ├── InputBar.qml           entrada multiline, Enter envia, Shift+Enter quebra linha
    ├── StatusPanel.qml        faixa de status real (CORE/MEMORY/AI/SESSION)
    ├── StatusIndicator.qml    ponto + label reutilizável
    ├── ActionButton.qml       botão genérico reutilizável (com tooltip embutido)
    └── PermissionOverlay.qml  pedido de permissão (READ/ACTION/DANGEROUS)
```

### `Theme.qml`

Nenhuma cor/espaçamento/duração é hardcoded fora daqui — sempre `Theme.*`.
Paleta: grafite/azul profundo/ciano frio/branco, com violeta só como accent
pontual (algumas partículas orbitais). Fonte: `Segoe UI Variable` com
fallback automático do próprio Windows para `Segoe UI`.

### `JarvisCore.qml` — o núcleo (AI Core)

O elemento visual central, desenhado inteiramente em QML (Qt Quick Shapes +
`Rectangle`s + animações nativas — **sem** GIF, vídeo ou pintura por frame
em Python): anéis concêntricos incompletos (arcos via `PathAngleArc`, cada
um com rotação/velocidade próprias), glow por círculos concêntricos
translúcidos (sem shader, para manter previsível), marcas radiais discretas,
partículas orbitais, e um núcleo central com pulso de "respiração".

Reage a estado real via três propriedades (`state`, `aiConfigured`,
`aiSessionActive`), vindas do Bridge:
- **IDLE** — movimento lento, respiração suave.
- **THINKING/WORKING/LISTENING/SPEAKING** — rotação mais rápida, pulso mais forte.
- **ERROR** — cor muda para o accent de erro (com transição suave) e volta
  sozinha quando o backend volta para `idle`.
- **Sem IA configurada/sessão inativa** — brilho reduzido (`dim`), mas o
  núcleo nunca "morre" nem some.

### Boot, resize, fullscreen

Sequência de entrada curta (~0,9s): núcleo aparece primeiro, painéis (chat/
status/input) em seguida — só cosmético, nunca bloqueia o backend (que
inicia em paralelo, de forma totalmente independente). `F11` alterna
fullscreen, `Esc` sai dele. Janela sem moldura nativa (`FramelessWindowHint`):
mover usa `Window.startSystemMove()`, redimensionar pelas bordas usa
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

Nenhuma resposta de IA é simulada — só estados visuais e o overlay de
permissão, para poder validar a interface sem depender de uma sessão real.

## Testes

- `tests/test_bridge.py` — offline, com `JarvisApplication` real sobre
  `FakeAIService` (mesmos fakes usados pelo backend). Sem GUI, sem QML.
- `tests/test_qml_smoke.py` — confirma que `Main.qml` carrega sem
  erros/warnings, com `QT_QPA_PLATFORM=offscreen`. **Não** testa pixels —
  isso é responsabilidade de inspeção visual manual.

## Limitações desta versão

- Sem streaming real (o contrato de eventos já suporta; falta só ligar
  `response.delta` quando o Agent SDK real estiver conectado).
- Sem voz, sem MCP, sem ferramentas reais, sem Ruflo — tudo isso continua
  planejado, não implementado.
- Sem tela de configurações, sem temas alternativos, sem persistência de
  janela (posição/tamanho não são lembrados entre execuções).
- Validação visual "de verdade" (like, olhar para a tela e avaliar
  acabamento) precisa ser feita por quem está rodando — os testes
  automatizados cobrem comportamento e ausência de erros, não estética.
