import QtQuick
import QtQuick.Shapes
import "../theme"

// AI Core / Orb — o elemento visual central do JARVIS. Puramente declarativo
// (Shapes + Rectangles + animações nativas do Qt Quick, sem pintura por
// frame em Python, sem GIF/vídeo). Reage a `state`/`aiConfigured`/
// `aiSessionActive` reais — nada aqui é só decorativo.
Item {
    id: core

    // --- API pública ---
    property string state: "idle"          // idle | thinking | working | listening | speaking | waiting_confirmation | error
    property bool aiConfigured: false
    property bool aiSessionActive: false
    property real size: 320

    function play() { booted = true }      // dispara a animação de entrada

    // --- Estado interno derivado ---
    // idle | thinking | working | listening | speaking | waiting_confirmation | error
    // são os únicos estados válidos hoje (app/state.py); `active`/`alert`
    // abaixo já cobrem os que ainda não são produzidos em runtime (listening/
    // speaking/working), então ligar esses estados no futuro não exige
    // mexer neste componente.
    readonly property bool active: state === "thinking" || state === "working" || state === "listening" || state === "speaking"
    readonly property bool errored: state === "error"
    // Pulso mais rápido tanto em atividade real quanto em erro (alerta) —
    // waiting_confirmation fica de fora de propósito: é espera, não processamento.
    readonly property bool alert: active || errored
    property color accent: Theme.stateColor(state)
    // Offline nunca deve parecer "morto" — só um pouco mais discreto (v0.6:
    // 0.72, era 0.68 — o núcleo ficava legível demais apagado no v0.5).
    readonly property real dim: (aiConfigured && aiSessionActive) ? 1.0 : 0.72
    readonly property int ringSpeed: active ? 3200 : 13000
    property bool booted: false

    implicitWidth: size
    implicitHeight: size

    opacity: booted ? 1 : 0
    scale: booted ? 1 : 0.8
    Behavior on opacity { NumberAnimation { duration: Theme.durationBoot; easing.type: Easing.OutCubic } }
    Behavior on scale { NumberAnimation { duration: Theme.durationBoot; easing.type: Easing.OutBack; easing.overshoot: 1.15 } }
    Behavior on accent { ColorAnimation { duration: Theme.durationSlow } }

    // ---------------------------------------------------------------
    // Camada 1 — glow ambiente (círculos concêntricos translúcidos,
    // sem shader/blur: previsível e leve).
    // ---------------------------------------------------------------
    Repeater {
        model: [
            { scale: 2.1, opacity: 0.035 },
            { scale: 1.7, opacity: 0.055 },
            { scale: 1.4, opacity: 0.08 },
            { scale: 1.16, opacity: 0.13 }
        ]
        delegate: Rectangle {
            required property var modelData
            anchors.centerIn: parent
            width: core.size * modelData.scale
            height: width
            radius: width / 2
            color: core.accent
            opacity: modelData.opacity * core.dim
            Behavior on color { ColorAnimation { duration: Theme.durationSlow } }
            Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
        }
    }

    // ---------------------------------------------------------------
    // Camada 2 — anéis (arcos incompletos), cada um com rotação e
    // velocidade próprias.
    // ---------------------------------------------------------------
    Shape {
        id: ringOuter
        anchors.centerIn: parent
        width: core.size * 0.94
        height: width
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        opacity: 0.5 * core.dim
        Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
        ShapePath {
            fillColor: "transparent"
            strokeColor: core.accent
            strokeWidth: 1.4
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ringOuter.width / 2; centerY: ringOuter.height / 2
                radiusX: ringOuter.width / 2 - 2; radiusY: radiusX
                startAngle: -40; sweepAngle: 240
            }
        }
        RotationAnimation on rotation {
            from: 0; to: 360
            duration: core.ringSpeed * 1.35
            loops: Animation.Infinite
            running: core.booted
        }
    }

    Shape {
        id: ringMiddle
        anchors.centerIn: parent
        width: core.size * 0.76
        height: width
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        opacity: 0.42 * core.dim
        Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
        ShapePath {
            fillColor: "transparent"
            strokeColor: Theme.blue
            strokeWidth: 1.2
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ringMiddle.width / 2; centerY: ringMiddle.height / 2
                radiusX: ringMiddle.width / 2 - 2; radiusY: radiusX
                startAngle: 60; sweepAngle: 170
            }
        }
        RotationAnimation on rotation {
            from: 360; to: 0
            duration: core.ringSpeed
            loops: Animation.Infinite
            running: core.booted
        }
    }

    Shape {
        id: ringInner
        anchors.centerIn: parent
        width: core.size * 0.58
        height: width
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        opacity: 0.35 * core.dim
        Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
        ShapePath {
            fillColor: "transparent"
            strokeColor: core.accent
            strokeWidth: 1
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ringInner.width / 2; centerY: ringInner.height / 2
                radiusX: ringInner.width / 2 - 2; radiusY: radiusX
                startAngle: 200; sweepAngle: 90
            }
        }
        // Congela no erro ("segmentos interrompem movimento") em vez de um
        // flash — retoma sozinho quando o estado sai de `error`.
        RotationAnimation on rotation {
            from: 0; to: -360
            duration: core.ringSpeed * 0.8
            loops: Animation.Infinite
            running: core.booted && !core.errored
        }
    }

    // ---------------------------------------------------------------
    // Camada 3 — anel segmentado (ticks discretos, como um dial de dados),
    // girando devagar e numa velocidade própria — profundidade extra sem
    // virar bagunça visual.
    // ---------------------------------------------------------------
    Item {
        id: segmentRing
        anchors.fill: parent
        opacity: core.dim
        RotationAnimation on rotation {
            from: 0; to: -360
            duration: core.ringSpeed * 2.1
            loops: Animation.Infinite
            running: core.booted
        }
        Repeater {
            model: 16
            delegate: Item {
                required property int index
                anchors.fill: parent
                rotation: index * (360 / 16)
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: parent.height * 0.02
                    width: 1.4
                    height: index % 4 === 0 ? 9 : 5
                    radius: 0.7
                    color: index % 8 === 4 ? Theme.violet : core.accent
                    opacity: index % 4 === 0 ? 0.34 : 0.16
                    Behavior on color { ColorAnimation { duration: Theme.durationSlow } }
                }
            }
        }
    }

    // ---------------------------------------------------------------
    // Camada 4 — partículas orbitais.
    // ---------------------------------------------------------------
    Repeater {
        model: 6
        delegate: Item {
            id: orbitAnchor
            required property int index
            anchors.centerIn: parent
            width: 1
            height: 1

            RotationAnimation on rotation {
                from: orbitAnchor.index * 61
                to: orbitAnchor.index * 61 + (orbitAnchor.index % 2 === 0 ? 360 : -360)
                duration: core.ringSpeed * 0.5 + orbitAnchor.index * 500
                loops: Animation.Infinite
                running: core.booted
            }

            Rectangle {
                x: core.size * (0.31 + 0.045 * (orbitAnchor.index % 3))
                y: -width / 2
                width: orbitAnchor.index % 3 === 0 ? 3 : 2
                height: width
                radius: width / 2
                color: orbitAnchor.index % 4 === 0 ? Theme.violet : core.accent
                opacity: 0.55 * core.dim
                Behavior on color { ColorAnimation { duration: Theme.durationSlow } }
            }
        }
    }

    // ---------------------------------------------------------------
    // Camada 5 — núcleo central (respiração / pulso).
    // ---------------------------------------------------------------
    Rectangle {
        id: coreHalo
        anchors.centerIn: parent
        width: core.size * 0.26
        height: width
        radius: width / 2
        color: core.accent
        opacity: 0.35 * core.dim
        Behavior on color { ColorAnimation { duration: Theme.durationSlow } }

        SequentialAnimation on scale {
            loops: Animation.Infinite
            running: core.booted
            NumberAnimation { to: core.alert ? 1.2 : 1.08; duration: core.alert ? 480 : 1700; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: core.alert ? 480 : 1700; easing.type: Easing.InOutSine }
        }
    }

    Rectangle {
        id: coreDot
        anchors.centerIn: parent
        width: core.size * 0.13
        height: width
        radius: width / 2
        color: core.accent
        opacity: 0.95 * core.dim
        Behavior on color { ColorAnimation { duration: Theme.durationSlow } }

        SequentialAnimation on scale {
            loops: Animation.Infinite
            running: core.booted
            NumberAnimation { to: core.alert ? 1.12 : 1.05; duration: core.alert ? 480 : 1700; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: core.alert ? 480 : 1700; easing.type: Easing.InOutSine }
        }
    }

    Rectangle {
        anchors.centerIn: parent
        width: core.size * 0.05
        height: width
        radius: width / 2
        color: Theme.textPrimary
        opacity: 0.85 * core.dim
    }
}
