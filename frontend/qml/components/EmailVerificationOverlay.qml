import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Verificação de e-mail (v1.0). Os dois contadores (expiração do código e
// liberação do reenvio) são derivados de timestamps REAIS vindos do backend
// (`expires_at`/`resend_available_at`, persistidos no banco) — o Timer daqui
// só decrementa a exibição. Fechar e reabrir o JARVIS mostra o tempo real
// restante, nunca um contador reiniciado (ver services/email_verification_service.py).
Item {
    id: overlay

    property bool open: false
    property string maskedEmail: ""
    property int secondsUntilExpiry: 0
    property int secondsUntilResend: 0
    property bool busy: false
    property string errorMessage: ""
    property bool emailConfigured: true

    signal submitCode(string code)
    signal resendRequested()
    signal closeRequested()

    readonly property bool _canResend: secondsUntilResend <= 0 && !busy && emailConfigured
    readonly property bool _expired: secondsUntilExpiry <= 0

    function _two(value) { return value < 10 ? "0" + value : "" + value }
    function _clock(totalSeconds) {
        const s = Math.max(0, totalSeconds)
        return _two(Math.floor(s / 60)) + ":" + _two(s % 60)
    }

    onOpenChanged: if (open) { codeField.text = ""; codeField.forceActiveFocus() }
    onErrorMessageChanged: if (errorMessage.length > 0) overlay.busy = false

    // Decrementa a exibição a cada segundo. A autoridade continua sendo o
    // backend: `secondsUntilExpiry`/`secondsUntilResend` são reescritos pelo
    // Bridge sempre que o desafio muda.
    Timer {
        running: overlay.open && (overlay.secondsUntilExpiry > 0 || overlay.secondsUntilResend > 0)
        interval: 1000
        repeat: true
        onTriggered: {
            if (overlay.secondsUntilExpiry > 0) overlay.secondsUntilExpiry--
            if (overlay.secondsUntilResend > 0) overlay.secondsUntilResend--
        }
    }

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    Keys.onEscapePressed: overlay.closeRequested()

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.78)
        MouseArea { anchors.fill: parent; onClicked: overlay.closeRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(420, overlay.width - Theme.spacingXl * 2)
        anchors.centerIn: parent
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.cyan
        scale: overlay.open ? 1 : 0.94
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

        MouseArea { anchors.fill: parent }

        Column {
            id: column
            x: Theme.spacingLg
            y: Theme.spacingLg
            width: card.width - Theme.spacingLg * 2
            spacing: Theme.spacingMd

            Text {
                width: parent.width
                text: "VERIFIQUE SEU E-MAIL"
                color: Theme.textSecondary
                font.family: Theme.fontFamily
                font.pixelSize: 12
                font.weight: Font.DemiBold
                font.letterSpacing: Theme.letterSpacingLabel * 1.3
                wrapMode: Text.Wrap
            }

            Column {
                width: parent.width
                spacing: 2
                Text {
                    width: parent.width
                    text: "Código enviado para:"
                    color: Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                Text {
                    width: parent.width
                    text: overlay.maskedEmail
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
            }

            // --- Campo do código ---
            Rectangle {
                width: parent.width
                height: 48
                radius: Theme.radiusMedium
                color: Theme.surface
                border.width: 1
                border.color: codeField.activeFocus ? Theme.borderFocus : Theme.borderSubtle
                Behavior on border.color { ColorAnimation { duration: Theme.durationFast } }

                TextField {
                    id: codeField
                    anchors.fill: parent
                    horizontalAlignment: TextInput.AlignHCenter
                    verticalAlignment: TextInput.AlignVCenter
                    placeholderText: "______"
                    placeholderTextColor: Theme.textFaint
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: 22
                    font.letterSpacing: 8
                    maximumLength: 6
                    inputMethodHints: Qt.ImhDigitsOnly
                    validator: RegularExpressionValidator { regularExpression: /[0-9]{0,6}/ }
                    background: null
                    enabled: !overlay.busy && !overlay._expired
                    onAccepted: if (text.length === 6) { overlay.busy = true; overlay.submitCode(text) }
                }
            }

            Text {
                width: parent.width
                visible: overlay.errorMessage.length > 0
                text: overlay.errorMessage
                color: Theme.danger
                font.family: Theme.fontFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // --- Contadores reais ---
            Column {
                width: parent.width
                spacing: 3
                visible: overlay.emailConfigured

                Text {
                    width: parent.width
                    text: overlay._expired
                        ? "Código expirado — peça um novo."
                        : "Código expira em " + overlay._clock(overlay.secondsUntilExpiry)
                    color: overlay._expired ? Theme.warning : Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
                Text {
                    width: parent.width
                    visible: overlay.secondsUntilResend > 0
                    text: "Reenviar em " + overlay.secondsUntilResend + "s"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
            }

            Text {
                width: parent.width
                visible: !overlay.emailConfigured
                text: "O envio de e-mail não está configurado neste ambiente (JARVIS_SMTP_HOST / JARVIS_EMAIL_FROM). "
                    + "A conta continua funcionando normalmente sem verificação."
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // `ModalButtonRow` (Flow) e não `Row`: três botões não cabem em
            // uma linha só no cartão de 420px — ver ModalButtonRow.qml.
            ModalButtonRow {
                ActionButton {
                    label: overlay.busy ? "VERIFICANDO..." : "VERIFICAR"
                    emphasis: true
                    enabled: !overlay.busy && !overlay._expired && codeField.text.length === 6
                    onClicked: { overlay.busy = true; overlay.submitCode(codeField.text) }
                }
                ActionButton {
                    label: "REENVIAR CÓDIGO"
                    emphasis: false
                    enabled: overlay._canResend
                    onClicked: { overlay.errorMessage = ""; overlay.resendRequested() }
                }
                ActionButton {
                    label: "AGORA NÃO"
                    emphasis: false
                    onClicked: overlay.closeRequested()
                }
            }
        }
    }
}
