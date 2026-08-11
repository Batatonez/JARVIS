import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Campo de texto de uma linha para telas de autenticação (AuthScreen.qml) —
// label discreta acima, mesmo acabamento visual do resto do HUD (não é um
// <input> de formulário web genérico).
Item {
    id: root

    property string label: ""
    property bool isPassword: false
    property alias text: field.text
    signal accepted()

    implicitHeight: column.implicitHeight

    Column {
        id: column
        width: parent.width
        spacing: 6

        Text {
            text: root.label
            color: Theme.textFaint
            font.family: Theme.fontFamily
            font.pixelSize: 11
            font.letterSpacing: Theme.letterSpacingLabel
        }

        Rectangle {
            width: parent.width
            height: 42
            radius: Theme.radiusMedium
            color: Theme.surfaceElevated
            border.width: 1
            border.color: field.activeFocus ? Theme.borderFocus : (hoverHandler.hovered ? Theme.borderStrong : Theme.borderSubtle)
            Behavior on border.color { ColorAnimation { duration: Theme.durationFast } }

            HoverHandler { id: hoverHandler }

            TextField {
                id: field
                anchors.fill: parent
                anchors.leftMargin: Theme.spacingMd
                anchors.rightMargin: Theme.spacingMd
                verticalAlignment: TextInput.AlignVCenter
                echoMode: root.isPassword ? TextInput.Password : TextInput.Normal
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: 14
                selectByMouse: true
                background: null
                padding: 0
                onAccepted: root.accepted()
            }
        }
    }
}
