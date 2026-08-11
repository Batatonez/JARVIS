import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Sidebar retrátil (v0.9): novo chat, busca, histórico agrupado por data,
// conta/plano no rodapé. Segue o mesmo design system do resto do HUD — não
// é um clone visual de ChatGPT/Claude. Toda ação passa por sinais; quem
// fala com o Bridge é sempre o Main.qml, nunca este componente diretamente.
Item {
    id: root

    property bool expanded: true
    property var conversations: []
    property string currentConversationId: ""
    property var currentUser: null // {id, username, displayName, plan} ou null

    signal newConversationRequested()
    signal conversationSelected(string conversationId)
    signal deleteRequested(string conversationId)
    signal searchTextChanged(string query)
    signal accountClicked()
    signal toggleRequested()

    readonly property int _expandedWidth: 264
    readonly property int _collapsedWidth: 60

    width: expanded ? _expandedWidth : _collapsedWidth
    Behavior on width { NumberAnimation { duration: Theme.durationSlow; easing.type: Easing.OutCubic } }
    clip: true

    property var _groups: []
    function _recomputeGroups() {
        const now = new Date()
        const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
        const buckets = { hoje: [], ontem: [], semana: [], antigos: [] }
        for (let i = 0; i < root.conversations.length; i++) {
            const c = root.conversations[i]
            const updated = new Date(c.updatedAt)
            const updatedDay = new Date(updated.getFullYear(), updated.getMonth(), updated.getDate())
            const diffDays = Math.floor((startOfToday - updatedDay) / 86400000)
            if (diffDays <= 0) buckets.hoje.push(c)
            else if (diffDays === 1) buckets.ontem.push(c)
            else if (diffDays <= 7) buckets.semana.push(c)
            else buckets.antigos.push(c)
        }
        const labels = [["hoje", "HOJE"], ["ontem", "ONTEM"], ["semana", "ÚLTIMOS 7 DIAS"], ["antigos", "MAIS ANTIGOS"]]
        const result = []
        for (let i = 0; i < labels.length; i++) {
            const key = labels[i][0]
            if (buckets[key].length > 0) result.push({ label: labels[i][1], items: buckets[key] })
        }
        root._groups = result
    }
    onConversationsChanged: _recomputeGroups()
    Component.onCompleted: _recomputeGroups()

    Rectangle {
        anchors.fill: parent
        color: Theme.surfacePanel
        border.width: 1
        border.color: Theme.borderSubtle
    }

    Column {
        anchors.fill: parent
        anchors.margins: Theme.spacingSm
        spacing: Theme.spacingMd

        // --- Cabeçalho: só o toggle (o wordmark "JARVIS" já está na title
        // bar logo acima — repetir aqui seria redundante). ---
        Item {
            width: parent.width
            height: 32
            SidebarIconButton {
                anchors.right: root.expanded ? parent.right : undefined
                anchors.horizontalCenter: root.expanded ? undefined : parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                glyph: "collapse"
                flipped: !root.expanded
                tooltip: root.expanded ? "Recolher barra lateral" : "Expandir barra lateral"
                onClicked: root.toggleRequested()
            }
        }

        // --- Novo chat ---
        Rectangle {
            width: parent.width
            height: 38
            radius: Theme.radiusMedium
            color: newChatHover.hovered ? Theme.surfaceHover : "transparent"
            border.width: 1
            border.color: Theme.borderSubtle
            Behavior on color { ColorAnimation { duration: Theme.durationFast } }

            HoverHandler { id: newChatHover }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.newConversationRequested()
            }

            Row {
                anchors.centerIn: parent
                spacing: Theme.spacingSm
                Text {
                    text: "+"
                    color: Theme.cyan
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                }
                Text {
                    visible: root.expanded
                    text: "NOVO CHAT"
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel
                }
            }
            ToolTip.visible: !root.expanded && newChatHover.hovered
            ToolTip.text: "Novo chat"
            ToolTip.delay: 500
        }

        // --- Busca ---
        Rectangle {
            width: parent.width
            height: 34
            visible: root.expanded
            radius: Theme.radiusMedium
            color: Theme.surfaceElevated
            border.width: 1
            border.color: searchField.activeFocus ? Theme.borderFocus : Theme.borderSubtle
            Behavior on border.color { ColorAnimation { duration: Theme.durationFast } }

            TextField {
                id: searchField
                anchors.fill: parent
                anchors.leftMargin: Theme.spacingSm
                anchors.rightMargin: Theme.spacingSm
                verticalAlignment: TextInput.AlignVCenter
                placeholderText: "Buscar"
                placeholderTextColor: Theme.textFaint
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: 12
                background: null
                padding: 0
                onTextChanged: root.searchTextChanged(text)
            }
        }

        // --- Lista de conversas (agrupada por data) ---
        Flickable {
            width: parent.width
            height: Math.max(0, parent.height - y)
            visible: root.expanded
            contentWidth: width
            contentHeight: list.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: list
                width: parent.width
                spacing: Theme.spacingMd

                Text {
                    width: parent.width
                    visible: root.conversations.length === 0
                    text: "Nenhuma conversa ainda"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                Repeater {
                    model: root._groups
                    delegate: Column {
                        required property var modelData
                        width: list.width
                        spacing: 2

                        Text {
                            text: modelData.label
                            color: Theme.textFaint
                            font.family: Theme.fontFamily
                            font.pixelSize: 10
                            font.letterSpacing: Theme.letterSpacingLabel
                            leftPadding: Theme.spacingXs
                        }

                        Repeater {
                            model: modelData.items
                            delegate: SidebarConversationRow {
                                required property var modelData
                                width: list.width
                                title: modelData.title
                                active: modelData.id === root.currentConversationId
                                onClicked: root.conversationSelected(modelData.id)
                                onDeleteClicked: root.deleteRequested(modelData.id)
                            }
                        }
                    }
                }
            }
        }

        Item { width: 1; visible: !root.expanded; height: Math.max(0, parent.height - y - 46) }
    }

    // --- Conta / plano (rodapé) ---
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.spacingSm
        height: 46
        radius: Theme.radiusMedium
        color: accountHover.hovered ? Theme.surfaceHover : "transparent"
        border.width: 1
        border.color: Theme.borderSubtle
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }

        HoverHandler { id: accountHover }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.accountClicked()
        }

        Row {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Theme.spacingSm
            spacing: Theme.spacingSm

            Rectangle {
                width: 26; height: 26; radius: 13
                anchors.verticalCenter: parent.verticalCenter
                color: Theme.surfaceElevated
                border.width: 1
                border.color: Theme.cyan
                Text {
                    anchors.centerIn: parent
                    text: root.currentUser ? root.currentUser.displayName.charAt(0).toUpperCase() : "?"
                    color: Theme.cyan
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
            }
            Column {
                visible: root.expanded
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Text {
                    text: root.currentUser ? root.currentUser.displayName : ""
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    width: 150
                }
                Text {
                    text: root.currentUser ? root.currentUser.plan.toUpperCase() : ""
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 10
                    font.letterSpacing: Theme.letterSpacingLabel
                }
            }
        }
        ToolTip.visible: !root.expanded && accountHover.hovered
        ToolTip.text: root.currentUser ? (root.currentUser.displayName + " · " + root.currentUser.plan.toUpperCase()) : "Conta"
        ToolTip.delay: 500
    }
}
