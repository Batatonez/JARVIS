import QtQuick
import QtQuick.Window
import "../theme"

// Title bar própria, integrada ao HUD. Usa as APIs nativas do Qt para
// mover (`startSystemMove`) e redimensionar (`startSystemResize`) a janela
// frameless — nada de contas manuais com posição global do mouse.
Item {
    id: titleBar

    property Window targetWindow
    property string subtitle: ""
    readonly property bool maximized: targetWindow ? targetWindow.visibility === Window.Maximized : false

    implicitHeight: 40

    // Arrastar em qualquer área vazia da barra move a janela; duplo-clique
    // maximiza/restaura.
    MouseArea {
        anchors.fill: parent
        anchors.rightMargin: controlsRow.width + Theme.spacingSm
        acceptedButtons: Qt.LeftButton
        onPressed: (mouse) => {
            if (titleBar.targetWindow) titleBar.targetWindow.startSystemMove()
        }
        onDoubleClicked: {
            if (!titleBar.targetWindow) return
            titleBar.targetWindow.visibility = titleBar.maximized ? Window.Windowed : Window.Maximized
        }
    }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLg
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spacingSm

        Rectangle {
            width: 7; height: 7; radius: 3.5
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.cyan
        }
        Text {
            text: "JARVIS"
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: 14
            font.weight: Font.DemiBold
            font.letterSpacing: Theme.letterSpacingLabel
        }
    }

    Text {
        anchors.centerIn: parent
        text: titleBar.subtitle
        color: Theme.textFaint
        font.family: Theme.fontFamily
        font.pixelSize: 11
        font.letterSpacing: Theme.letterSpacingLabel
        visible: text.length > 0
    }

    Row {
        id: controlsRow
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2

        WindowButton {
            glyph: "minimize"
            onClicked: if (titleBar.targetWindow) titleBar.targetWindow.showMinimized()
            accessibleText: "Minimizar"
        }
        WindowButton {
            glyph: titleBar.maximized ? "restore" : "maximize"
            onClicked: {
                if (!titleBar.targetWindow) return
                titleBar.targetWindow.visibility = titleBar.maximized ? Window.Windowed : Window.Maximized
            }
            accessibleText: titleBar.maximized ? "Restaurar" : "Maximizar"
        }
        WindowButton {
            glyph: "close"
            danger: true
            onClicked: if (titleBar.targetWindow) titleBar.targetWindow.close()
            accessibleText: "Fechar"
        }
    }
}
