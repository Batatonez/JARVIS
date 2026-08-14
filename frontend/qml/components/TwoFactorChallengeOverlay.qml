import QtQuick
import "../theme"

// Segundo fator no LOGIN (v1.3, item 52).
//
// Este overlay aparece DEPOIS de a senha ser aceita e ANTES de qualquer
// sessão existir. Enquanto ele está aberto, `bridge.authenticated` continua
// falso: não há sessão, não há chats carregados, não há nada.
//
// Aceita tanto o código do autenticador quanto um código de recuperação — o
// backend tenta os dois no mesmo campo, então não forçamos o usuário a
// escolher qual está digitando.
Item {
    id: overlay

    property bool open: false
    property string errorText: ""

    signal submitCode(string code)
    signal cancelRequested()

    onOpenChanged: if (open) { codeField.text = ""; codeField.forceActiveFocus() }

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    Keys.onEscapePressed: overlay.cancelRequested()

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.86)
        MouseArea { anchors.fill: parent }
    }

    Rectangle {
        id: card
        width: Math.min(380, overlay.width - Theme.spacingXl * 2)
        anchors.centerIn: parent
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.violet
        scale: overlay.open ? 1 : 0.94
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

        MouseArea { anchors.fill: parent }

        Column {
            id: column
            x: Theme.spacingLg
            y: Theme.spacingLg
            width: card.width - Theme.spacingLg * 2
            spacing: Theme.spacingMd

            Row {
                spacing: Theme.spacingSm
                Rectangle {
                    width: 8; height: 8; radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.violet
                }
                Text {
                    text: "VERIFICAÇÃO EM DUAS ETAPAS"
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel * 1.3
                }
            }

            Text {
                width: parent.width
                text: "Digite o código do seu aplicativo autenticador. Se você perdeu o acesso a ele, "
                    + "use um dos seus códigos de recuperação."
                color: Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            AuthField {
                id: codeField
                width: parent.width
                label: "CÓDIGO"
                onAccepted: overlay.submitCode(text)
            }

            Text {
                width: parent.width
                visible: overlay.errorText.length > 0
                text: overlay.errorText
                color: Theme.danger
                font.family: Theme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            ModalButtonRow {
                ActionButton {
                    label: "ENTRAR"
                    emphasis: true
                    tint: Theme.violet
                    onClicked: overlay.submitCode(codeField.text)
                }
                ActionButton {
                    label: "CANCELAR"
                    emphasis: false
                    onClicked: overlay.cancelRequested()
                }
            }
        }
    }
}
