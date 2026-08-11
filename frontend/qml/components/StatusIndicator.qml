import QtQuick
import "../theme"

// Um indicador de status: ponto colorido + label. Usado na StatusPanel.
// `tone` define a cor sem depender só de cor para transmitir sentido — o
// texto do label já diz o estado por extenso.
Row {
    id: indicator

    property string label: ""
    property string value: ""
    property string tone: "muted" // ready | active | attention | danger | muted

    spacing: Theme.spacingSm

    readonly property color toneColor: {
        if (tone === "ready") return Theme.success
        if (tone === "active") return Theme.cyan
        if (tone === "attention") return Theme.warning
        if (tone === "danger") return Theme.danger
        return Theme.textFaint
    }
    readonly property bool isMuted: tone === "muted"

    Rectangle {
        id: dot
        width: 8; height: 8; radius: 4
        anchors.verticalCenter: parent.verticalCenter
        color: indicator.isMuted ? "transparent" : indicator.toneColor
        border.width: indicator.isMuted ? 1 : 0
        border.color: Theme.borderStrong
        Behavior on color { ColorAnimation { duration: Theme.durationNormal } }

        SequentialAnimation on opacity {
            running: indicator.tone === "active"
            loops: Animation.Infinite
            NumberAnimation { to: 0.45; duration: 900; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
        }
    }

    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: indicator.label
        color: Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: 11
        font.letterSpacing: Theme.letterSpacingLabel
    }

    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: indicator.value
        color: indicator.isMuted ? Theme.textSecondary : indicator.toneColor
        font.family: Theme.fontFamily
        font.pixelSize: 11
        font.weight: indicator.isMuted ? Font.Normal : Font.DemiBold
        font.letterSpacing: Theme.letterSpacingLabel
    }
}
