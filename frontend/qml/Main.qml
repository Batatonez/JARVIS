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
    // Breakpoint único e simples (v0.8): abaixo disso, StatusPanel prioriza
    // CORE/AI/VOICE e compacta o resto — não é um sistema de layout CSS-like.
    readonly property bool compact: width < 1250
    // Boot em etapas (~1.3s no total): title bar quase instantânea, ponto
    // semente, núcleo energiza, região core+chat, status, input.
    property bool titleBooted: false
    property bool rowBooted: false
    property bool statusBooted: false
    property bool inputBooted: false

    // --- Contas/sidebar (v0.9) ---
    property bool sidebarExpanded: true
    property bool accountPanelOpen: false
    property bool voiceSetupOpen: false
    property bool emailVerificationOpen: false

    // --- v1.3 ---
    property bool voiceInputOpen: false
    property bool accountSettingsOpen: false
    // Texto do "Test Microphone": vive só aqui, nunca vira mensagem do chat
    // nem é enviado à IA (item 16).
    property string microphoneHeardText: ""
    property string accountErrorText: ""
    property string accountSuccessText: ""
    property string twoFactorErrorText: ""
    property string sidebarSearchQuery: ""
    readonly property var sidebarConversations: sidebarSearchQuery.length > 0
        ? bridge.searchConversations(sidebarSearchQuery)
        : bridge.conversations

    // Compartilhado entre o clique do MicButton e o atalho Ctrl+Space —
    // SETUP_REQUIRED abre o fluxo de instalação em vez de tentar gravar
    // (ver services/stt_service.py::STTStatus e VoiceSetupOverlay.qml).
    function handleMicToggle() {
        if (bridge.sttStatus === "setup_required") {
            window.voiceSetupOpen = true
        } else {
            bridge.toggleListening()
        }
    }

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

        // --- v1.3 ---
        function onMicrophoneTestFinished(text) {
            // Só exibe no painel de voz. NÃO entra no chat, NÃO vai para a IA.
            window.microphoneHeardText = text
        }
        function onAccountErrorRaised(message) {
            // O erro do 2FA aparece na própria tela de ativação quando ela
            // está aberta; nas demais, na faixa de mensagens da tela de conta.
            if (bridge.twoFactorEnrollment) {
                window.twoFactorErrorText = message
            } else {
                window.accountErrorText = message
                window.accountSuccessText = ""
            }
        }
        function onAccountUpdated(message) {
            window.accountSuccessText = message
            window.accountErrorText = ""
            window.twoFactorErrorText = ""
        }
        function onAccountDeleted() {
            window.accountSettingsOpen = false
            window.accountErrorText = ""
            window.accountSuccessText = ""
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
    Shortcut { sequence: "Ctrl+Shift+5"; enabled: bridge.devMode; onActivated: bridge.simulateState("waiting_confirmation") }
    Shortcut { sequence: "Ctrl+Shift+6"; enabled: bridge.devMode; onActivated: bridge.simulateState("listening") }
    Shortcut { sequence: "Ctrl+Shift+7"; enabled: bridge.devMode; onActivated: bridge.simulateState("speaking") }
    Shortcut { sequence: "Ctrl+Shift+8"; enabled: bridge.devMode; onActivated: bridge.simulateState("processing_speech") }

    // Push-to-talk por clique: Ctrl+Space liga/desliga o microfone — só
    // funciona com a janela do JARVIS em foco (Shortcut do Qt é por janela,
    // não um hotkey global do Windows; ver frontend/README.md, seção Voz).
    Shortcut {
        sequence: "Ctrl+Space"
        enabled: bridge.voiceAvailable || bridge.sttStatus === "setup_required"
            || bridge.jarvisState === "listening" || bridge.jarvisState === "processing_speech"
        onActivated: window.handleMicToggle()
    }

    // ------------------------------------------------------------------
    // Fundo: quase preto, com vinheta muito sutil e grid discreto.
    // ------------------------------------------------------------------
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.backgroundSecondary }
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
            online: bridge.running
            opacity: window.titleBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        }

        AuthScreen {
            id: authScreen
            objectName: "authScreen"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !bridge.authenticated
            onLoginRequested: (username, password) => bridge.login(username, password)
            onRegisterRequested: (username, displayName, email, password) => bridge.register(username, displayName, email, password)
            // v1.5 — disponibilidade e força de senha: a tela pergunta, o
            // backend responde. Ela nunca decide nada disso sozinha.
            passwordAssessment: bridge.passwordAssessment
            onUsernameAvailabilityRequested: (username) => bridge.checkUsernameAvailability(username)
            onEmailAvailabilityRequested: (email) => bridge.checkEmailAvailability(email)
            onPasswordAssessmentRequested: (password, username, email, displayName) =>
                bridge.assessPassword(password, username, email, displayName)
            Connections {
                target: bridge
                function onIdentityAvailabilityChanged(field, available, message) {
                    authScreen.applyAvailability(field, available, message)
                }
            }
        }

        RowLayout {
            id: hudRow
            objectName: "hudRow"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: bridge.authenticated
            spacing: 0

            Sidebar {
                id: sidebar
                objectName: "sidebar"
                Layout.fillHeight: true
                expanded: window.sidebarExpanded
                conversations: window.sidebarConversations
                currentConversationId: bridge.currentConversationId
                currentUser: bridge.currentUser
                onNewConversationRequested: bridge.startNewConversation()
                onConversationSelected: (conversationId) => bridge.openConversation(conversationId)
                onDeleteRequested: (conversationId) => bridge.deleteConversation(conversationId)
                onRenameRequested: (conversationId, newTitle) => bridge.renameConversation(conversationId, newTitle)
                onSearchTextChanged: (query) => window.sidebarSearchQuery = query
                onAccountClicked: window.accountPanelOpen = true
                onToggleRequested: window.sidebarExpanded = !window.sidebarExpanded
            }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: Theme.spacingXl
            Layout.rightMargin: Theme.spacingXl
            Layout.topMargin: Theme.spacingMd
            spacing: Theme.spacingXl
            opacity: window.rowBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }

            // -- Painel do núcleo (protagonista) --
            ColumnLayout {
                // Referencia `window.width` (estável, externo a este layout)
                // em vez de `parent.width` (o próprio RowLayout que está
                // sendo calculado) — evita ciclo de rearranjo.
                Layout.preferredWidth: window.width * 0.46
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignVCenter
                spacing: Theme.spacingLg

                Item { Layout.fillHeight: true }

                Item {
                    id: coreStage
                    Layout.alignment: Qt.AlignHCenter
                    implicitWidth: core.implicitWidth
                    implicitHeight: core.implicitHeight

                    // Ponto semente: aparece primeiro (~t=0), some assim que
                    // o núcleo de verdade começa a "energizar" — o primeiro
                    // batimento da sequência de boot.
                    Rectangle {
                        anchors.centerIn: parent
                        width: 6; height: 6; radius: 3
                        color: Theme.primaryBright
                        opacity: core.booted ? 0 : 0.9
                        scale: core.booted ? 2.4 : 1
                        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                    }

                    JarvisCore {
                        id: core
                        objectName: "jarvisCore"
                        anchors.centerIn: parent
                        // Cresce com a tela (altura manda, já que a coluna é
                        // aproximadamente quadrada) — nunca fica minúsculo em
                        // monitores grandes, nem estoura a coluna em janelas
                        // pequenas.
                        size: Math.max(250, Math.min(520, Math.min(window.width, window.height) * 0.38))
                        state: bridge.jarvisState
                        aiConfigured: bridge.aiConfigured
                        aiSessionActive: bridge.aiSessionActive
                        voiceLevel: bridge.voiceLevel
                    }
                }

                Column {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 6

                    Row {
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: 7
                        Rectangle {
                            width: 6; height: 6; radius: 3
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.stateColor(bridge.jarvisState)
                            Behavior on color { ColorAnimation { duration: Theme.durationNormal } }
                        }
                        Text {
                            text: bridge.jarvisState.toUpperCase()
                            color: Theme.stateColor(bridge.jarvisState)
                            font.family: Theme.fontFamily
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            font.letterSpacing: Theme.letterSpacingLabel * 2
                            Behavior on color { ColorAnimation { duration: Theme.durationNormal } }
                        }
                    }
                    // v1.1: estado público da IA, não o nome do provider.
                    // "OPENROUTER (FREE)" é detalhe técnico — continua
                    // disponível em log/diagnóstico/testes (`bridge.aiBackend`),
                    // mas não é o que o usuário final precisa ler aqui.
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: !bridge.aiConfigured ? "AI · NOT CONFIGURED"
                            : bridge.jarvisState === "error" ? "AI · ERROR"
                            : bridge.jarvisState === "thinking" ? "AI · THINKING"
                            : "AI · CONFIGURED"
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.letterSpacing: Theme.letterSpacingLabel
                    }

                    // Linha de atividade discreta — separa "informação" de
                    // "ação" (o botão logo abaixo) sem precisar de um
                    // divisor rígido cortando o HUD.
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 28; height: 1
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: "transparent" }
                            GradientStop { position: 0.5; color: Theme.borderStrong }
                            GradientStop { position: 1.0; color: "transparent" }
                        }
                    }
                }

                Item { Layout.fillHeight: true }
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
                    pending: bridge.busy
                    onCopyRequested: (rawText) => bridge.copyToClipboard(rawText)
                    onRegenerateRequested: (messageId) => bridge.regenerateMessage(messageId)
                }
            }
        }

        StatusPanel {
            id: statusPanel
            objectName: "statusPanel"
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacingXl
            Layout.topMargin: Theme.spacingLg
            running: bridge.running
            memoryAvailable: bridge.memoryAvailable
            aiConfigured: bridge.aiConfigured
            aiSessionActive: bridge.aiSessionActive
            jarvisState: bridge.jarvisState
            voiceAvailable: bridge.voiceAvailable
            ttsReady: bridge.ttsReady
            voiceOutputEnabled: bridge.voiceOutputEnabled
            aiProvider: bridge.aiProvider
            aiModel: bridge.aiModel
            aiFallbackUsed: bridge.aiFallbackUsed
            aiFallbackCount: bridge.aiFallbackCount
            compact: window.compact
            onVoiceOutputToggleRequested: bridge.setVoiceOutputEnabled(!bridge.voiceOutputEnabled)
            opacity: window.statusBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }
        }

        Item {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.spacingXl
            Layout.rightMargin: Theme.spacingXl
            Layout.topMargin: Theme.spacingSm
            Layout.bottomMargin: Theme.spacingLg
            Layout.preferredHeight: inputBar.implicitHeight
            opacity: window.inputBooted ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }

            InputBar {
                id: inputBar
                anchors.left: parent.left
                anchors.right: parent.right
                busy: bridge.busy
                voiceState: bridge.jarvisState
                sttStatus: bridge.sttStatus
                // Mic ausente e falha ao abrir o dispositivo são situações
                // diferentes (ver frontend/README.md, seção Voz) — a
                // tooltip do MicButton nunca deve "desaparecer misteriosamente".
                // SETUP_REQUIRED tem sua própria tooltip fixa (MicButton.qml)
                // e não usa esta mensagem.
                voiceUnavailableReason: bridge.sttStatus === "no_microphone"
                    ? "Nenhum microfone encontrado."
                    : "Não foi possível abrir o microfone."
                speaking: bridge.jarvisState === "speaking"
                onSendRequested: (text) => bridge.sendMessage(text)
                onCancelRequested: bridge.cancelCurrentRequest()
                onMicToggleRequested: window.handleMicToggle()
                onStopSpeakingRequested: bridge.stopSpeaking()
            }

            // Nível de voz real do microfone — só existe enquanto LISTENING
            // (nunca simulado; ver Waveform.qml).
            Waveform {
                anchors.bottom: inputBar.top
                anchors.bottomMargin: 10
                anchors.right: inputBar.right
                anchors.rightMargin: 56
                visible: bridge.jarvisState === "listening"
                level: bridge.voiceLevel
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
        function onVoiceErrorRaised(message) {
            busyHint.text = message
            busyHint.opacity = 1
            busyHintTimer.restart()
        }
        function onTranscriptionReady(text) {
            inputBar.insertTranscription(text)
        }
        function onAuthErrorRaised(message) {
            // Com o desafio de segundo fator aberto, o erro é DELE (código
            // errado / rate limit) — mostrar na tela de login por baixo
            // deixaria a mensagem escondida atrás do overlay.
            if (bridge.awaitingTwoFactor) {
                window.twoFactorErrorText = message
            } else {
                authScreen.errorMessage = message
            }
        }
        function onVoiceModelInstalledChanged() {
            if (bridge.voiceModelInstalled) window.voiceSetupOpen = false
        }
        function onVerificationErrorRaised(message) {
            // Se o overlay está aberto, o erro aparece nele (contexto certo);
            // senão cai no aviso discreto do rodapé.
            if (window.emailVerificationOpen) {
                emailVerification.errorMessage = message
            } else {
                busyHint.text = message
                busyHint.opacity = 1
                busyHintTimer.restart()
            }
        }
        function onVerificationSucceeded() {
            emailVerification.errorMessage = ""
            window.emailVerificationOpen = false
            busyHint.text = "E-mail verificado."
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
        objectName: "permissionOverlay"
        anchors.fill: parent
        z: 100
        request: bridge.pendingPermission
        onApproved: (id) => bridge.approvePermission(id)
        onDenied: (id) => bridge.denyPermission(id)
    }

    AccountPanel {
        objectName: "accountPanel"
        anchors.fill: parent
        z: 100
        open: window.accountPanelOpen
        user: bridge.currentUser
        onCloseRequested: window.accountPanelOpen = false
        onLogoutRequested: {
            window.accountPanelOpen = false
            bridge.logout()
        }
        onVerifyEmailRequested: {
            window.accountPanelOpen = false
            window.emailVerificationOpen = true
            // Só pede um código novo se não houver um ativo — respeitar o
            // cooldown é responsabilidade do backend, mas evitar a chamada
            // inútil aqui poupa um erro visível ao usuário.
            if (bridge.verificationSecondsUntilExpiry <= 0) bridge.requestVerificationCode()
        }
        onSettingsRequested: {
            window.accountPanelOpen = false
            window.accountSettingsOpen = true
            bridge.refreshTwoFactorStatus()
            bridge.refreshSessions()
        }
        onVoiceSettingsRequested: {
            window.accountPanelOpen = false
            window.voiceInputOpen = true
            bridge.refreshAudioDevices()
        }
    }

    // ------------------------------------------------------------------
    // Overlays da v1.3. Todos seguem a mesma regra: o QML só reflete o que o
    // Bridge publica e devolve a intenção do usuário — nenhuma decisão de
    // segurança acontece aqui (item 60).
    // ------------------------------------------------------------------

    VoiceInputOverlay {
        objectName: "voiceInputOverlay"
        anchors.fill: parent
        z: 100
        open: window.voiceInputOpen
        devices: bridge.audioDevices
        selectedKey: bridge.selectedMicrophoneKey
        fellBack: bridge.microphoneFellBack
        engineName: bridge.sttEngine
        sttStatus: bridge.sttStatus
        level: bridge.voiceLevel
        testActive: bridge.microphoneTestActive
        heardText: window.microphoneHeardText
        onDeviceSelected: (key) => bridge.selectMicrophone(key)
        onRefreshRequested: bridge.refreshAudioDevices()
        onTestRequested: {
            window.microphoneHeardText = ""
            bridge.testMicrophone()
        }
        onCloseRequested: {
            window.voiceInputOpen = false
            window.microphoneHeardText = ""
        }
    }

    AccountSettingsOverlay {
        id: accountSettings
        objectName: "accountSettingsOverlay"
        anchors.fill: parent
        z: 100
        open: window.accountSettingsOpen
        user: bridge.currentUser
        reauthValid: bridge.reauthValid
        twoFactor: bridge.twoFactorStatus
        sessions: bridge.activeSessions
        pendingEmailChange: bridge.pendingEmailChange
        securityEvents: bridge.securityEvents
        providers: bridge.aiProviders
        providerTestActive: bridge.providerTestActive
        locale: bridge.locale
        localeOptions: bridge.localeOptions
        strings: bridge.strings
        errorText: window.accountErrorText
        successText: window.accountSuccessText
        onConfirmPasswordRequested: (password) => bridge.confirmPassword(password)
        onDisplayNameChangeRequested: (name) => bridge.changeDisplayName(name)
        onUsernameChangeRequested: (username) => bridge.changeUsername(username)
        onPasswordChangeRequested: (current, next, confirm) => bridge.changePassword(current, next, confirm)
        onEmailChangeRequested: (email) => bridge.requestEmailChange(email)
        onEmailChangeConfirmRequested: (code) => bridge.confirmEmailChange(code)
        onTwoFactorEnrollmentRequested: bridge.startTwoFactorEnrollment()
        onTwoFactorDisableRequested: (code) => bridge.disableTwoFactor(code)
        onRecoveryRegenerateRequested: (code) => bridge.regenerateRecoveryCodes(code)
        onSessionsRefreshRequested: bridge.refreshSessions()
        onLogOutOthersRequested: bridge.logOutOtherSessions()
        onSessionRevokeRequested: (sessionId) => bridge.revokeSession(sessionId)
        onSecurityEventsRefreshRequested: bridge.refreshSecurityEvents()
        onProvidersRefreshRequested: bridge.refreshProviders()
        onProviderTestRequested: (providerId) => bridge.testProviderConnection(providerId)
        onLocalePreferencesRequested: (language, region, currency) =>
            bridge.setLocalePreferences(language, region, currency)
        onDeleteAccountRequested: (password, confirmation, code) => bridge.deleteAccount(password, confirmation, code)
        onCloseRequested: {
            window.accountSettingsOpen = false
            window.accountErrorText = ""
            window.accountSuccessText = ""
        }
    }

    TwoFactorSetupOverlay {
        objectName: "twoFactorSetupOverlay"
        anchors.fill: parent
        z: 101
        open: !!bridge.twoFactorEnrollment
        enrollment: bridge.twoFactorEnrollment
        errorText: window.twoFactorErrorText
        onConfirmRequested: (code) => {
            window.twoFactorErrorText = ""
            bridge.confirmTwoFactorEnrollment(code)
        }
        onCancelRequested: {
            window.twoFactorErrorText = ""
            bridge.cancelTwoFactorEnrollment()
        }
    }

    RecoveryCodesOverlay {
        objectName: "recoveryCodesOverlay"
        anchors.fill: parent
        z: 102
        open: !!(bridge.recoveryCodes && bridge.recoveryCodes.length > 0)
        codes: bridge.recoveryCodes
        onCloseRequested: bridge.clearRecoveryCodes()
    }

    TwoFactorChallengeOverlay {
        objectName: "twoFactorChallengeOverlay"
        anchors.fill: parent
        z: 103
        open: bridge.awaitingTwoFactor
        errorText: window.twoFactorErrorText
        onSubmitCode: (code) => {
            window.twoFactorErrorText = ""
            bridge.submitTwoFactorCode(code)
        }
        onCancelRequested: {
            window.twoFactorErrorText = ""
            bridge.cancelTwoFactor()
        }
    }

    VoiceSetupOverlay {
        objectName: "voiceSetupOverlay"
        anchors.fill: parent
        z: 100
        open: window.voiceSetupOpen
        modelInfo: bridge.voiceModelInfo
        downloadActive: bridge.voiceModelDownloadActive
        downloadProgress: bridge.voiceModelDownloadProgress
        onDownloadRequested: bridge.downloadVoiceModel()
        onCancelRequested: bridge.cancelVoiceModelDownload()
        onCloseRequested: window.voiceSetupOpen = false
    }

    EmailVerificationOverlay {
        id: emailVerification
        objectName: "emailVerificationOverlay"
        anchors.fill: parent
        z: 100
        open: window.emailVerificationOpen
        maskedEmail: bridge.currentUser ? bridge.currentUser.maskedEmail : ""
        emailConfigured: bridge.emailServiceConfigured
        secondsUntilExpiry: bridge.verificationSecondsUntilExpiry
        secondsUntilResend: bridge.verificationSecondsUntilResend
        onSubmitCode: (code) => bridge.verifyEmailCode(code)
        onResendRequested: bridge.requestVerificationCode()
        onCloseRequested: window.emailVerificationOpen = false
    }

    // ------------------------------------------------------------------
    // Acabamento da janela: borda externa de 1px extremamente sutil — só
    // decorativa (não consome mouse), não afeta resize/Snap. Cantos
    // customizados foram deliberadamente deixados de fora nesta versão:
    // arredondar uma Window frameless exige torná-la translúcida
    // (WA_TranslucentBackground), o que arrisca o comportamento de
    // resize/Snap já validado — comportamento funcional > estética.
    // ------------------------------------------------------------------
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.width: 1
        border.color: Qt.rgba(0.361, 0.882, 0.902, 0.10)
        z: 101
    }

    // ------------------------------------------------------------------
    // Overlay técnico — só em devMode (JARVIS_DEV=1), nunca para o usuário
    // final. Não é uma feature: existe só para validar estado visualmente
    // durante desenvolvimento sem precisar instrumentar QML toda vez.
    // ------------------------------------------------------------------
    Column {
        visible: bridge.devMode
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 48
        anchors.rightMargin: Theme.spacingMd
        spacing: 2
        z: 100
        opacity: 0.75

        Text {
            text: "STATE " + bridge.jarvisState
            color: Theme.textFaint
            font.family: Theme.fontFamily
            font.pixelSize: 10
        }
        Text {
            text: "BUSY " + bridge.busy
            color: Theme.textFaint
            font.family: Theme.fontFamily
            font.pixelSize: 10
        }
        Text {
            text: "VOICE LEVEL " + bridge.voiceLevel.toFixed(2)
            color: Theme.textFaint
            font.family: Theme.fontFamily
            font.pixelSize: 10
        }
    }

    // ------------------------------------------------------------------
    // Sequência de boot (~1.3s): title bar quase instantânea, ponto
    // semente, núcleo energiza, região core+chat, status, input. Nunca
    // bloqueia o backend — a Application Layer inicia em paralelo, de
    // forma totalmente independente desta animação cosmética.
    // ------------------------------------------------------------------
    Component.onCompleted: {
        titleBootTimer.start()
        coreBootTimer.start()
        rowBootTimer.start()
        statusBootTimer.start()
        inputBootTimer.start()
    }
    Timer { id: titleBootTimer; interval: 60; onTriggered: window.titleBooted = true }
    Timer { id: coreBootTimer; interval: 160; onTriggered: core.play() }
    Timer { id: rowBootTimer; interval: 420; onTriggered: window.rowBooted = true }
    Timer { id: statusBootTimer; interval: 820; onTriggered: window.statusBooted = true }
    Timer { id: inputBootTimer; interval: 1000; onTriggered: window.inputBooted = true }
}
