import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Uma linha de conversa na sidebar — título + ações (Rename/Delete) que só
// aparecem no hover, para não poluir a lista. Realce discreto quando é a
// conversa aberta no momento.
//
// v1.3, item 18 — RENAME INLINE: clicar em ✎ troca o título por um TextField
// na mesma linha. Enter confirma, Esc cancela, o texto é trimado e limitado,
// e quem persiste é o backend (`renameConversation`). Um diálogo modal só
// para renomear seria pesado demais para uma ação tão pequena.
Item {
    id: row

    property string title: ""
    property bool active: false
    property int maxTitleLength: 60

    signal clicked()
    signal deleteClicked()
    signal renamed(string newTitle)

    property bool editing: false

    implicitHeight: 30

    function startEditing() {
        editField.text = row.title
        row.editing = true
        editField.forceActiveFocus()
        editField.selectAll()
    }

    function _commit() {
        const cleaned = editField.text.trim()
        row.editing = false
        // Sem mudança real: não gasta uma escrita no banco nem marca a
        // conversa como "renomeada à mão" à toa.
        if (cleaned.length > 0 && cleaned !== row.title) {
            row.renamed(cleaned)
        }
    }

    function _cancel() {
        row.editing = false
        editField.text = row.title
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: row.active ? Qt.rgba(0.361, 0.882, 0.902, 0.10) : (hoverHandler.hovered ? Theme.surfaceHover : "transparent")
        border.width: row.active || row.editing ? 1 : 0
        border.color: row.editing ? Theme.cyan : Theme.borderFocus
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    HoverHandler { id: hoverHandler }
    MouseArea {
        anchors.fill: parent
        anchors.rightMargin: actions.visible ? actions.width : 0
        cursorShape: Qt.PointingHandCursor
        enabled: !row.editing
        onClicked: row.clicked()
    }

    Text {
        id: titleText
        anchors.left: parent.left
        anchors.right: actions.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingSm
        anchors.rightMargin: Theme.spacingXs
        visible: !row.editing
        text: row.title
        color: row.active ? Theme.textPrimary : Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: 12
        elide: Text.ElideRight
    }

    TextField {
        id: editField
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingSm
        anchors.rightMargin: Theme.spacingSm
        visible: row.editing
        maximumLength: row.maxTitleLength
        color: Theme.textPrimary
        font.family: Theme.fontFamily
        font.pixelSize: 12
        selectByMouse: true
        background: null
        padding: 0
        onAccepted: row._commit()
        Keys.onEscapePressed: row._cancel()
        // Clicar fora confirma o que foi digitado — comportamento esperado de
        // rename inline (a alternativa, descartar em silêncio, perde o texto).
        onActiveFocusChanged: if (!activeFocus && row.editing) row._commit()
    }

    Row {
        id: actions
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.rightMargin: Theme.spacingXs
        spacing: 2
        visible: hoverHandler.hovered && !row.editing

        Item {
            width: 20; height: 20
            Text {
                anchors.centerIn: parent
                text: "✎"
                color: renameHover.hovered ? Theme.cyan : Theme.textFaint
                font.pixelSize: 11
                Behavior on color { ColorAnimation { duration: Theme.durationFast } }
            }
            HoverHandler { id: renameHover }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: row.startEditing()
            }
            ToolTip.visible: renameHover.hovered
            ToolTip.text: "Renomear conversa"
            ToolTip.delay: 500
        }

        Item {
            width: 20; height: 20
            Text {
                anchors.centerIn: parent
                text: "✕"
                color: deleteHover.hovered ? Theme.danger : Theme.textFaint
                font.pixelSize: 10
                Behavior on color { ColorAnimation { duration: Theme.durationFast } }
            }
            HoverHandler { id: deleteHover }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: row.deleteClicked()
            }
            ToolTip.visible: deleteHover.hovered
            ToolTip.text: "Excluir conversa"
            ToolTip.delay: 500
        }
    }
}
