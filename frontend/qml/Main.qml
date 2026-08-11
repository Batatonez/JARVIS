import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import "theme"

Window {
    id: window

    width: 1440
    height: 900
    minimumWidth: 1100
    minimumHeight: 700
    visible: true
    color: Theme.background
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "JARVIS"

    readonly property bool isFullscreen: visibility === Window.FullScreen
    property bool uiBooted: false

    // ------------------------------------------------------------------
    // Encerramento limpo: intercepta o fechamento, deixa o Bridge encerrar
    // a Application Layer (cancela requisição pendente, fecha sessão de IA)
    // e só então deixa a janela fechar de verdade.
    // ------------------------------------------------------------------
    onClosing: (closeEvent) => {
        if (!bridge.canClose) {
            closeEvent.accepted = false
            bridge.requestShutdown()
        }
    }
    Connections {
        target: bridge
        function onCanCloseChanged() {
            if (bridge.canClose) window.close()
        }
    }

    Shortcut {
        sequence: "F11"
        onActivated: window.visibility = window.isFullscreen ? Window.Windowed : Window.FullScreen
    }
    Shortcut {
        sequence: "Esc"
        enabled: window.isFullscreen
        onActivated: window.visibility = Window.Windowed
    }

    // Somente para desenvolvimento (bridge.devMode) — nunca ativo para o
    // usuário final. Simula estados visuais sem chamada real de IA.
    Shortcut { sequence: "Ctrl+Shift+1"; enabled: bridge.devMode; onActivated: bridge.simulateState("idle") }
    Shortcut { sequence: "Ctrl+Shift+2"; enabled: bridge.devMode; onActivated: bridge.simulateState("thinking") }
    Shortcut { sequence: "Ctrl+Shift+3"; enabled: bridge.devMode; onActivated: bridge.simulateState("error") }
    Shortcut { sequence: "Ctrl+Shift+4"; enabled: bridge.devMode; onActivated: bridge.simulateState("permission") }

    // ------------------------------------------------------------------
    // Fundo: quase preto, com vinheta muito sutil e grid discreto.
    // ------------------------------------------------------------------
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0.08, 0.14, 0.20, 0.30) }
            GradientStop { position: 0.5; color: "transparent" }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.30) }
        }
    }

    Item {
        anchors.fill: parent
        opacity: 0.035
        Repeater {
            model: 7
            delegate: Rectangle {
                required property int index
                x: (window.width / 7) * index
                width: 1
                height: window.height
                color: Theme.cyan
            }
        }
        Repeater {
            model: 5
            delegate: Rectangle {
                required property int index
                y: (window.height / 5) * index
                width: window.width
                height: 1
                color: Theme.cyan
            }
        }
    }

    // ------------------------------------------------------------------
    // Layout principal
    // ------------------------------------------------------------------
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TitleBar {
            Layout.fillWidth: true
            targetWindow: window
            subtitle: bridge.running ? "CORE ONLINE" : "CORE STARTING"
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: Theme.spacingXl
            Layout.rightMargin: Theme.spacingXl
            Layout.topMargin: Theme.spacingMd
            spacing: Theme.spacingXl
            opacity: window.uiBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }

            // -- Painel do núcleo (protagonista) --
            ColumnLayout {
                // Referencia `window.width` (estável, externo a este layout)
                // em vez de `parent.width` (o próprio RowLayout que está
                // sendo calculado) — evita ciclo de rearranjo.
                Layout.preferredWidth: window.width * 0.4
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignVCenter
                spacing: Theme.spacingLg

                Item { Layout.fillHeight: true }

                JarvisCore {
                    id: core
                    Layout.alignment: Qt.AlignHCenter
                    size: Math.max(220, Math.min(320, window.width * 0.22))
                    state: bridge.jarvisState
                    aiConfigured: bridge.aiConfigured
                    aiSessionActive: bridge.aiSessionActive
                }

                Column {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 4
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: bridge.jarvisState.toUpperCase()
                        color: Theme.stateColor(bridge.jarvisState)
                        font.family: Theme.fontFamily
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel * 2
                        Behavior on color { ColorAnimation { duration: Theme.durationNormal } }
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: bridge.aiConfigured ? bridge.aiBackend.toUpperCase() : "AI OFFLINE"
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.letterSpacing: Theme.letterSpacingLabel
                    }
                }

                Item { Layout.fillHeight: true }

                ActionButton {
                    Layout.alignment: Qt.AlignHCenter
                    label: "NOVA CONVERSA"
                    tooltip: "Limpa a conversa atual e reinicia a sessão de IA (não apaga a memória)"
                    onClicked: bridge.newConversation()
                }

                Item { Layout.preferredHeight: Theme.spacingLg }
            }

            // -- Painel de conversa --
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Theme.spacingSm

                ChatPanel {
                    id: chatPanel
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: bridge.messages
                }
            }
        }

        StatusPanel {
            id: statusPanel
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacingXl
            Layout.topMargin: Theme.spacingLg
            running: bridge.running
            memoryAvailable: bridge.memoryAvailable
            aiConfigured: bridge.aiConfigured
            aiSessionActive: bridge.aiSessionActive
            aiBackend: bridge.aiBackend
            opacity: window.uiBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }
        }

        Item {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacingXl
            Layout.rightMargin: Theme.spacingXl
            Layout.topMargin: Theme.spacingSm
            Layout.bottomMargin: Theme.spacingLg
            Layout.preferredHeight: inputBar.implicitHeight
            opacity: window.uiBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }

            InputBar {
                id: inputBar
                anchors.left: parent.left
                anchors.right: parent.right
                busy: bridge.busy
                onSendRequested: (text) => bridge.sendMessage(text)
                onCancelRequested: bridge.cancelCurrentRequest()
            }

            Text {
                id: busyHint
                anchors.bottom: inputBar.top
                anchors.bottomMargin: 6
                anchors.left: inputBar.left
                text: "JARVIS está processando outra mensagem..."
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: 11
                opacity: 0
                visible: opacity > 0

                Timer {
                    id: busyHintTimer
                    interval: 2400
                    onTriggered: busyHint.opacity = 0
                }
                Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
            }
        }
    }

    Connections {
        target: bridge
        function onBusyRejected(message) {
            busyHint.text = message
            busyHint.opacity = 1
            busyHintTimer.restart()
        }
        function onInternalErrorRaised(message) {
            busyHint.text = message
            busyHint.opacity = 1
            busyHintTimer.restart()
        }
    }

    // ------------------------------------------------------------------
    // Redimensionamento pelas bordas (janela sem moldura nativa) — usa a
    // API oficial do Qt (startSystemResize), não contas manuais de mouse.
    // ------------------------------------------------------------------
    Item {
        anchors.fill: parent
        z: 10
        visible: window.visibility === Window.Windowed

        MouseArea {
            height: 4
            anchors { left: parent.left; right: parent.right; top: parent.top }
            cursorShape: Qt.SizeVerCursor
            onPressed: window.startSystemResize(Qt.TopEdge)
        }
        MouseArea {
            height: 4
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
            cursorShape: Qt.SizeVerCursor
            onPressed: window.startSystemResize(Qt.BottomEdge)
        }
        MouseArea {
            width: 4
            anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
            cursorShape: Qt.SizeHorCursor
            onPressed: window.startSystemResize(Qt.LeftEdge)
        }
        MouseArea {
            width: 4
            anchors { top: parent.top; bottom: parent.bottom; right: parent.right }
            cursorShape: Qt.SizeHorCursor
            onPressed: window.startSystemResize(Qt.RightEdge)
        }
        MouseArea {
            width: 8; height: 8
            anchors { top: parent.top; left: parent.left }
            cursorShape: Qt.SizeFDiagCursor
            onPressed: window.startSystemResize(Qt.TopEdge | Qt.LeftEdge)
        }
        MouseArea {
            width: 8; height: 8
            anchors { top: parent.top; right: parent.right }
            cursorShape: Qt.SizeBDiagCursor
            onPressed: window.startSystemResize(Qt.TopEdge | Qt.RightEdge)
        }
        MouseArea {
            width: 8; height: 8
            anchors { bottom: parent.bottom; left: parent.left }
            cursorShape: Qt.SizeBDiagCursor
            onPressed: window.startSystemResize(Qt.BottomEdge | Qt.LeftEdge)
        }
        MouseArea {
            width: 8; height: 8
            anchors { bottom: parent.bottom; right: parent.right }
            cursorShape: Qt.SizeFDiagCursor
            onPressed: window.startSystemResize(Qt.BottomEdge | Qt.RightEdge)
        }
    }

    PermissionOverlay {
        anchors.fill: parent
        z: 100
        request: bridge.pendingPermission
        onApproved: (id) => bridge.approvePermission(id)
        onDenied: (id) => bridge.denyPermission(id)
    }

    // ------------------------------------------------------------------
    // Sequência de boot: núcleo aparece, depois os painéis. Curta (~1s),
    // e nunca bloqueia o backend — a Application Layer inicia em paralelo,
    // de forma totalmente independente desta animação cosmética.
    // ------------------------------------------------------------------
    Component.onCompleted: {
        coreBootTimer.start()
        panelsBootTimer.start()
    }
    Timer { id: coreBootTimer; interval: 150; onTriggered: core.play() }
    Timer { id: panelsBootTimer; interval: 480; onTriggered: window.uiBooted = true }
}
