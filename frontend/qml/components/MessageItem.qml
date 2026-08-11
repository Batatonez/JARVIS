import QtQuick
import "../theme"

// Uma mensagem do chat: bloco discreto com linha lateral, não um balão
// grande estilo WhatsApp. JARVIS usa accent ciano; usuário usa um tom mais
// neutro. Texto é selecionável/copiável.
Item {
    id: item

    required property bool isUser
    required property string content
    required property string timestamp

    implicitWidth: parent ? parent.width : 400
    implicitHeight: column.implicitHeight + Theme.spacingMd

    readonly property color accent: isUser ? Theme.blue : Theme.cyan

    Rectangle {
        id: rail
        x: 0
        y: 2
        width: 2
        height: column.implicitHeight - 4
        radius: 1
        color: item.accent
        opacity: 0.55
    }

    Column {
        id: column
        x: Theme.spacingMd
        width: parent.width - Theme.spacingMd
        spacing: 3

        Row {
            spacing: Theme.spacingSm
            Text {
                text: item.isUser ? "YOU" : "JARVIS"
                color: item.accent
                font.family: Theme.fontFamily
                font.pixelSize: 11
                font.weight: Font.DemiBold
                font.letterSpacing: Theme.letterSpacingLabel
            }
            Text {
                text: item.timestamp
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 11
            }
        }

        TextEdit {
            width: column.width
            text: item.content
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: 14
            wrapMode: Text.Wrap
            readOnly: true
            selectByMouse: true
            selectionColor: Qt.rgba(0.361, 0.882, 0.902, 0.35)
            persistentSelection: true
            cursorVisible: false
        }
    }
}
