pragma Singleton
import QtQuick

// Tema central do JARVIS HUD. Nenhuma cor/espaçamento/duração deve ser
// hardcoded em outro QML — sempre referenciar Theme.*.
QtObject {
    // --- Paleta ---
    readonly property color background: "#06080D"
    readonly property color surface: "#0A111A"
    readonly property color surfaceElevated: "#0D1722"

    readonly property color cyan: "#5CE1E6"
    readonly property color blue: "#3B82F6"
    readonly property color violet: "#8B5CF6"

    readonly property color textPrimary: "#EAF6FF"
    readonly property color textMuted: "#8297A8"
    readonly property color textFaint: "#4E6072"

    readonly property color success: "#4ADE80"
    readonly property color warning: "#FBBF24"
    readonly property color danger: "#FF5C73"

    readonly property color borderSubtle: Qt.rgba(1, 1, 1, 0.07)
    readonly property color borderFocus: Qt.rgba(0.361, 0.882, 0.902, 0.45) // cyan translúcido

    // --- Geometria ---
    readonly property int radiusSmall: 6
    readonly property int radiusMedium: 12
    readonly property int radiusLarge: 20

    readonly property int spacingXs: 4
    readonly property int spacingSm: 8
    readonly property int spacingMd: 16
    readonly property int spacingLg: 28
    readonly property int spacingXl: 44

    // --- Tipografia ---
    // "Segoe UI Variable" só existe no Windows 11+; o mecanismo de
    // substituição de fontes do próprio Windows/Qt cai para "Segoe UI"
    // automaticamente quando a família exata não está instalada.
    readonly property string fontFamily: "Segoe UI Variable"
    readonly property real letterSpacingLabel: 1.1

    // --- Animação ---
    readonly property int durationFast: 120
    readonly property int durationNormal: 220
    readonly property int durationSlow: 420
    readonly property int durationBoot: 1200

    readonly property int easingStandard: Easing.OutCubic

    // --- Helpers de estado ---
    function stateColor(state) {
        if (state === "error") return danger
        if (state === "waiting_confirmation") return warning
        return cyan
    }

    function riskColor(riskLevel) {
        if (riskLevel === "dangerous") return danger
        if (riskLevel === "action") return warning
        return cyan
    }
}
