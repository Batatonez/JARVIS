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
    signal loginRequested(string username, string password)
    signal registerRequested(string username, string displayName, string email, string password)

    property string _mode: "choice" // choice | login | register

    onErrorMessageChanged: if (errorMessage.length > 0) root.busy = false

    function _reset() {
        loginUsername.text = ""
        loginPassword.text = ""
        registerUsername.text = ""
        registerDisplayName.text = ""
        registerEmail.text = ""
        registerPassword.text = ""
        root.errorMessage = ""
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

                AuthField { id: loginUsername; Layout.fillWidth: true; label: "USERNAME" }
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

                AuthField { id: registerUsername; Layout.fillWidth: true; label: "USERNAME" }
                AuthField { id: registerDisplayName; Layout.fillWidth: true; label: "NOME" }
                AuthField { id: registerEmail; Layout.fillWidth: true; label: "E-MAIL" }
                AuthField { id: registerPassword; Layout.fillWidth: true; label: "SENHA"; isPassword: true
                    onAccepted: registerSubmit.clicked()
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
                    enabled: !root.busy
                        && registerUsername.text.length > 0
                        && registerPassword.text.length > 0
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
