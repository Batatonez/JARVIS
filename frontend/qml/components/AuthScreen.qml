import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../theme"

// Tela de entrada (v0.9) — mostrada no lugar do HUD normal enquanto
// `bridge.authenticated` é falso. Três modos internos: escolha inicial
// (ENTRAR / CRIAR CONTA), formulário de login, formulário de criação de
// conta. Contas são 100% locais (SQLite) — nada disto fala com rede.
Item {
    id: root

    property bool busy: false
    property string errorMessage: ""
    // v1.5 — a decisão de "está disponível?" e "esta senha serve?" é SEMPRE
    // do backend. Estas properties só espelham a resposta dele; nada aqui
    // valida senha ou unicidade por conta própria, e o cadastro revalida tudo
    // no submit (o índice UNIQUE do banco continua sendo a autoridade final
    // contra corrida entre a consulta e o INSERT).
    property var passwordAssessment: null
    signal loginRequested(string username, string password)
    signal registerRequested(string username, string displayName, string email, string password)
    signal usernameAvailabilityRequested(string username)
    signal emailAvailabilityRequested(string email)
    signal passwordAssessmentRequested(string password, string username, string email, string displayName)

    property string _mode: "choice" // choice | login | register
    // "" = ainda não consultado, "checking", "ok", "taken"
    property string _usernameState: ""
    property string _usernameMessage: ""
    property string _emailState: ""
    property string _emailMessage: ""

    // Resposta do Bridge (`identityAvailabilityChanged`), roteada por campo.
    function applyAvailability(field, available, message) {
        if (field === "username") {
            root._usernameState = available ? "ok" : "taken"
            root._usernameMessage = message
        } else if (field === "email") {
            root._emailState = available ? "ok" : "taken"
            root._emailMessage = message
        }
    }

    onErrorMessageChanged: if (errorMessage.length > 0) root.busy = false

    function _reset() {
        loginUsername.text = ""
        loginPassword.text = ""
        registerUsername.text = ""
        registerDisplayName.text = ""
        registerEmail.text = ""
        registerPassword.text = ""
        root.errorMessage = ""
        root._usernameState = ""
        root._usernameMessage = ""
        root._emailState = ""
        root._emailMessage = ""
    }

    // Debounce: consultar a cada tecla geraria uma consulta por caractere sem
    // dar tempo de o usuário terminar de digitar. 400ms é o intervalo em que
    // a resposta ainda parece instantânea.
    Timer {
        id: usernameDebounce
        interval: 400
        onTriggered: root.usernameAvailabilityRequested(registerUsername.text.trim())
    }
    Timer {
        id: emailDebounce
        interval: 400
        onTriggered: root.emailAvailabilityRequested(registerEmail.text.trim())
    }
    Timer {
        id: passwordDebounce
        interval: 250
        onTriggered: root.passwordAssessmentRequested(
            registerPassword.text, registerUsername.text.trim(),
            registerEmail.text.trim(), registerDisplayName.text.trim())
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.backgroundSecondary }
            GradientStop { position: 1.0; color: Theme.background }
        }
    }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: Math.max(height, card.implicitHeight + Theme.spacingXl * 2)
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: card
            width: Math.min(380, root.width - Theme.spacingXl * 2)
            anchors.horizontalCenter: parent.horizontalCenter
            y: Math.max(Theme.spacingXl, (root.height - implicitHeight) / 2)
            spacing: Theme.spacingXl

            ColumnLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: Theme.spacingSm

                JarvisCore {
                    Layout.alignment: Qt.AlignHCenter
                    size: 96
                    state: "idle"
                    aiConfigured: false
                    aiSessionActive: false
                    Component.onCompleted: play()
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "JARVIS"
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel * 2
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "PERSONAL INTELLIGENCE SYSTEM"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.letterSpacing: Theme.letterSpacingLabel * 1.5
                }
            }

            // --- Escolha inicial ---
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                visible: root._mode === "choice"
                spacing: Theme.spacingMd

                ActionButton {
                    Layout.fillWidth: true
                    label: "ENTRAR"
                    emphasis: true
                    onClicked: { root._reset(); root._mode = "login" }
                }
                ActionButton {
                    Layout.fillWidth: true
                    label: "CRIAR CONTA"
                    emphasis: false
                    onClicked: { root._reset(); root._mode = "register" }
                }
            }

            // --- Login ---
            ColumnLayout {
                Layout.fillWidth: true
                visible: root._mode === "login"
                spacing: Theme.spacingMd

                // v1.3, item 38 — o mesmo campo aceita username OU e-mail;
                // quem resolve qual dos dois é o backend, numa query só.
                AuthField { id: loginUsername; Layout.fillWidth: true; label: "USERNAME OR EMAIL" }
                AuthField { id: loginPassword; Layout.fillWidth: true; label: "SENHA"; isPassword: true
                    onAccepted: loginSubmit.clicked()
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.errorMessage.length > 0 && root._mode === "login"
                    text: root.errorMessage
                    color: Theme.danger
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                ActionButton {
                    id: loginSubmit
                    Layout.fillWidth: true
                    label: root.busy ? "ENTRANDO..." : "ENTRAR"
                    emphasis: true
                    enabled: !root.busy && loginUsername.text.length > 0 && loginPassword.text.length > 0
                    onClicked: {
                        root.busy = true
                        root.errorMessage = ""
                        root.loginRequested(loginUsername.text, loginPassword.text)
                    }
                }
                ActionButton {
                    Layout.fillWidth: true
                    label: "VOLTAR"
                    emphasis: false
                    onClicked: { root._reset(); root._mode = "choice" }
                }
            }

            // --- Criar conta ---
            ColumnLayout {
                Layout.fillWidth: true
                visible: root._mode === "register"
                spacing: Theme.spacingMd

                AuthField {
                    id: registerUsername
                    Layout.fillWidth: true
                    label: "USERNAME"
                    onTextChanged: {
                        root._usernameState = text.trim().length > 0 ? "checking" : ""
                        root._usernameMessage = ""
                        usernameDebounce.restart()
                        passwordDebounce.restart()
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: root._usernameState.length > 0
                    text: root._usernameState === "checking"
                        ? "Verificando…"
                        : (root._usernameState === "ok" ? "✓ " : "✗ ") + root._usernameMessage
                    color: root._usernameState === "ok" ? Theme.success
                        : root._usernameState === "taken" ? Theme.danger : Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                AuthField {
                    id: registerDisplayName
                    Layout.fillWidth: true
                    label: "NOME"
                    onTextChanged: passwordDebounce.restart()
                }

                AuthField {
                    id: registerEmail
                    Layout.fillWidth: true
                    label: "E-MAIL"
                    onTextChanged: {
                        root._emailState = text.trim().length > 0 ? "checking" : ""
                        root._emailMessage = ""
                        emailDebounce.restart()
                        passwordDebounce.restart()
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: root._emailState.length > 0
                    text: root._emailState === "checking"
                        ? "Verificando…"
                        : (root._emailState === "ok" ? "✓ " : "✗ ") + root._emailMessage
                    color: root._emailState === "ok" ? Theme.success
                        : root._emailState === "taken" ? Theme.danger : Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                AuthField { id: registerPassword; Layout.fillWidth: true; label: "SENHA"; isPassword: true
                    onAccepted: registerSubmit.clicked()
                    onTextChanged: passwordDebounce.restart()
                }

                // Indicador de força: rótulo + as três checagens da política.
                // A senha em si nunca é exibida, logada nem devolvida — o que
                // chega aqui do Bridge é só o resultado da avaliação.
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    visible: registerPassword.text.length > 0 && !!root.passwordAssessment

                    Text {
                        Layout.fillWidth: true
                        readonly property string _level: root.passwordAssessment
                            ? root.passwordAssessment.strength : "weak"
                        text: "FORÇA: " + (_level === "strong" ? "FORTE"
                            : _level === "medium" ? "MÉDIA" : "FRACA")
                        color: _level === "strong" ? Theme.success
                            : _level === "medium" ? Theme.warning : Theme.danger
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel
                    }
                    Repeater {
                        model: root.passwordAssessment ? root.passwordAssessment.requirements : []
                        Text {
                            required property var modelData
                            Layout.fillWidth: true
                            text: (modelData.satisfied ? "✓ " : "✗ ") + modelData.label
                            color: modelData.satisfied ? Theme.success : Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.errorMessage.length > 0 && root._mode === "register"
                    text: root.errorMessage
                    color: Theme.danger
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                ActionButton {
                    id: registerSubmit
                    Layout.fillWidth: true
                    label: root.busy ? "CRIANDO..." : "CRIAR CONTA"
                    emphasis: true
                    // Validação de e-mail deliberadamente mínima (tem "@" com
                    // algo dos dois lados): validar e-mail por regex no cliente
                    // rejeita endereços legítimos e não prova nada — quem prova
                    // que o endereço existe é a verificação por código.
                    // A UI só bloqueia o que já sabe estar errado; ela nunca é
                    // a garantia. `AccountService`/`create_user` revalidam
                    // username, e-mail e senha no submit, e o índice UNIQUE do
                    // banco decide a corrida — ver services/user_repository.py.
                    enabled: !root.busy
                        && registerUsername.text.length > 0
                        && registerPassword.text.length > 0
                        && root._usernameState !== "taken"
                        && root._emailState !== "taken"
                        && !!(root.passwordAssessment && root.passwordAssessment.acceptable)
                        && /^[^@\s]+@[^@\s]+$/.test(registerEmail.text.trim())
                    onClicked: {
                        root.busy = true
                        root.errorMessage = ""
                        const name = registerDisplayName.text.length > 0 ? registerDisplayName.text : registerUsername.text
                        root.registerRequested(registerUsername.text, name, registerEmail.text.trim(), registerPassword.text)
                    }
                }
                ActionButton {
                    Layout.fillWidth: true
                    label: "VOLTAR"
                    emphasis: false
                    onClicked: { root._reset(); root._mode = "choice" }
                }
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Theme.spacingMd
                text: "Conta local — seus dados ficam neste computador."
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 10
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
