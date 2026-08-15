import QtQuick
import "../theme"

// ACCOUNT (v1.3, itens 30-32, 40-45, 54-57) — Profile / Security / Sessions /
// Danger Zone numa única tela com abas.
//
// REGRA CENTRAL (item 60): este arquivo NÃO decide nada de segurança. Ele não
// sabe qual conta está logada, se a reautenticação vale, se o 2FA está ativo
// ou se um e-mail está livre. Tudo isso chega pronto do Bridge e volta como
// pedido. Cada erro exibido é a mensagem que o backend devolveu.
Item {
    id: overlay

    property bool open: false
    property var user: null              // {username, displayName, email, maskedEmail, emailVerified, twoFactorEnabled}
    property bool reauthValid: false
    property var twoFactor: null         // {enabled, enrollmentPending, recoveryCodesRemaining, secretProtected, lockoutSeconds}
    property var sessions: []            // [{id, device, platform, createdAt, lastUsedAt, isCurrent}]
    property var pendingEmailChange: null
    property string errorText: ""
    property string successText: ""
    // v1.5 — atividade de segurança e providers de IA. Ambos chegam prontos e
    // já sanitizados do Bridge: nenhuma API key, token ou segredo atravessa
    // esta fronteira, e este arquivo não sabe o que é uma credencial.
    property var securityEvents: []      // [{id, type, label, at, detail}]
    property var providers: []           // [{id, label, enabled, configured, configurationLabel, health, healthLabel, models, position}]
    property bool providerTestActive: false
    // v1.6 — idioma/região/moeda. Tudo já resolvido pelo backend: esta tela
    // não lê locale do sistema, não deriva moeda e não traduz nada sozinha.
    property var locale: null            // {language, languageName, languageIsAuto, region, regionName, currency, currencySymbol, detectedLocale, ...}
    property var localeOptions: null     // {languages: [{value,label}], regions: [...], currencies: [...]}
    property var strings: ({})           // catálogo de tradução do idioma vigente

    // Texto traduzido com fallback para a própria chave — um rótulo vazio
    // seria um bug invisível; a chave crua aparecendo é diagnosticável.
    function tr(key, fallback) {
        if (overlay.strings && overlay.strings[key])
            return overlay.strings[key]
        return fallback !== undefined ? fallback : key
    }

    signal confirmPasswordRequested(string password)
    signal displayNameChangeRequested(string displayName)
    signal usernameChangeRequested(string username)
    signal passwordChangeRequested(string current, string next, string confirm)
    signal emailChangeRequested(string email)
    signal emailChangeConfirmRequested(string code)
    signal twoFactorEnrollmentRequested()
    signal twoFactorDisableRequested(string code)
    signal recoveryRegenerateRequested(string code)
    signal sessionsRefreshRequested()
    signal logOutOthersRequested()
    signal sessionRevokeRequested(string sessionId)
    signal securityEventsRefreshRequested()
    signal providersRefreshRequested()
    signal providerTestRequested(string providerId)
    signal localePreferencesRequested(string language, string region, string currency)
    signal deleteAccountRequested(string password, string confirmation, string code)
    signal closeRequested()

    property int _tab: 0
    readonly property var _tabs: [
        overlay.tr("account.profile", "PROFILE"),
        overlay.tr("account.security", "SECURITY"),
        overlay.tr("account.sessions", "SESSIONS"),
        overlay.tr("account.activity", "ACTIVITY"),
        overlay.tr("account.providers", "AI PROVIDERS"),
        overlay.tr("settings.language_region", "LANGUAGE & REGION"),
        overlay.tr("account.danger_zone", "DANGER ZONE")
    ]

    function _reset() {
        errorText = ""
        successText = ""
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
        width: Math.min(520, overlay.width - Theme.spacingXl * 2)
        height: Math.min(content.implicitHeight + Theme.spacingLg * 2, overlay.height - Theme.spacingXl * 2)
        anchors.centerIn: parent
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.blue
        scale: overlay.open ? 1 : 0.94
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

        MouseArea { anchors.fill: parent }

        Flickable {
            anchors.fill: parent
            anchors.margins: Theme.spacingLg
            contentHeight: content.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: content
                width: parent.width
                spacing: Theme.spacingMd

                Row {
                    spacing: Theme.spacingSm
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.blue
                    }
                    Text {
                        text: "ACCOUNT"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel * 1.3
                    }
                }

                // Abas: Flow em vez de Row para quebrarem linha numa janela
                // estreita, mesmo motivo do ModalButtonRow (v1.2).
                Flow {
                    width: parent.width
                    spacing: Theme.spacingSm
                    Repeater {
                        model: overlay._tabs
                        Rectangle {
                            required property int index
                            required property string modelData
                            radius: Theme.radiusSmall
                            height: 26
                            width: tabLabel.implicitWidth + Theme.spacingMd * 2
                            color: overlay._tab === index ? Theme.surface : "transparent"
                            border.width: 1
                            border.color: overlay._tab === index ? Theme.blue : Theme.borderSubtle
                            Text {
                                id: tabLabel
                                anchors.centerIn: parent
                                text: modelData
                                color: overlay._tab === index ? Theme.textPrimary : Theme.textFaint
                                font.family: Theme.fontFamily
                                font.pixelSize: 10
                                font.weight: Font.DemiBold
                                font.letterSpacing: Theme.letterSpacingLabel
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { overlay._tab = index; overlay._reset() }
                            }
                        }
                    }
                }

                // --- Faixa de reautenticação (item 45) ---
                Rectangle {
                    width: parent.width
                    // Só nas abas que executam operação sensível. ACTIVITY e
                    // AI PROVIDERS são leitura (e um teste de conexão que não
                    // toca a conta), então pedir a senha ali seria ritual sem
                    // função — e ritual sem função ensina o usuário a digitar
                    // a senha sem pensar.
                    // ACTIVITY, AI PROVIDERS e LANGUAGE & REGION são leitura
                    // ou preferência de interface — pedir a senha ali seria
                    // ritual sem função, e ritual sem função ensina o usuário
                    // a digitar a senha sem pensar.
                    visible: overlay._tab === 1 || overlay._tab === 2 || overlay._tab === 6
                    height: reauthColumn.implicitHeight + Theme.spacingSm * 2
                    radius: Theme.radiusSmall
                    color: Theme.surfacePanel
                    border.width: 1
                    border.color: overlay.reauthValid ? Theme.success : Theme.borderSubtle

                    Column {
                        id: reauthColumn
                        x: Theme.spacingSm
                        y: Theme.spacingSm
                        width: parent.width - Theme.spacingSm * 2
                        spacing: 6

                        Text {
                            width: parent.width
                            text: overlay.reauthValid
                                ? "Senha confirmada — operações sensíveis liberadas por alguns minutos."
                                : "Confirme sua senha para alterar dados sensíveis."
                            color: overlay.reauthValid ? Theme.success : Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AuthField {
                            id: reauthField
                            width: parent.width
                            visible: !overlay.reauthValid
                            label: "SENHA ATUAL"
                            isPassword: true
                            onAccepted: overlay.confirmPasswordRequested(text)
                        }
                        ActionButton {
                            visible: !overlay.reauthValid
                            label: "CONFIRMAR SENHA"
                            emphasis: true
                            tint: Theme.blue
                            onClicked: overlay.confirmPasswordRequested(reauthField.text)
                        }
                    }
                }

                // ------------------------------------------------ PROFILE
                Column {
                    width: parent.width
                    spacing: Theme.spacingMd
                    visible: overlay._tab === 0

                    AuthField {
                        id: displayNameField
                        width: parent.width
                        label: "DISPLAY NAME"
                        text: overlay.user ? overlay.user.displayName : ""
                    }
                    ActionButton {
                        label: "SALVAR NOME"
                        emphasis: false
                        onClicked: overlay.displayNameChangeRequested(displayNameField.text)
                    }

                    AuthField {
                        id: usernameField
                        width: parent.width
                        label: "USERNAME"
                        text: overlay.user ? overlay.user.username : ""
                    }
                    ActionButton {
                        label: "SALVAR USERNAME"
                        emphasis: false
                        onClicked: overlay.usernameChangeRequested(usernameField.text)
                    }

                    Item {
                        width: parent.width
                        implicitHeight: Math.max(emailLabel.implicitHeight, emailValue.implicitHeight)
                        Text {
                            id: emailLabel
                            anchors.left: parent.left
                            text: "E-MAIL ATUAL"
                            color: Theme.textFaint
                            font.family: Theme.fontFamily
                            font.pixelSize: 11
                            font.letterSpacing: Theme.letterSpacingLabel
                        }
                        Text {
                            id: emailValue
                            anchors.right: parent.right
                            anchors.left: emailLabel.right
                            anchors.leftMargin: Theme.spacingSm
                            text: overlay.user && overlay.user.email
                                ? overlay.user.email + (overlay.user.emailVerified ? "  ✓" : "  (não verificado)")
                                : "—"
                            color: overlay.user && overlay.user.emailVerified ? Theme.success : Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignRight
                            elide: Text.ElideRight
                        }
                    }

                    AuthField {
                        id: newEmailField
                        width: parent.width
                        label: "NOVO E-MAIL"
                    }
                    ActionButton {
                        label: "ENVIAR CÓDIGO AO NOVO E-MAIL"
                        emphasis: false
                        onClicked: overlay.emailChangeRequested(newEmailField.text)
                    }

                    Column {
                        width: parent.width
                        spacing: 6
                        visible: !!overlay.pendingEmailChange
                        Text {
                            width: parent.width
                            text: overlay.pendingEmailChange
                                ? "Código enviado para " + overlay.pendingEmailChange.maskedEmail
                                  + ". Expira em " + overlay.pendingEmailChange.secondsUntilExpiry + "s."
                                : ""
                            color: Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        AuthField {
                            id: emailCodeField
                            width: parent.width
                            label: "CÓDIGO"
                            onAccepted: overlay.emailChangeConfirmRequested(text)
                        }
                        ActionButton {
                            label: "CONFIRMAR TROCA DE E-MAIL"
                            emphasis: true
                            tint: Theme.blue
                            onClicked: overlay.emailChangeConfirmRequested(emailCodeField.text)
                        }
                    }
                }

                // ----------------------------------------------- SECURITY
                Column {
                    width: parent.width
                    spacing: Theme.spacingMd
                    visible: overlay._tab === 1

                    Text {
                        text: "TROCAR SENHA"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel
                    }
                    AuthField { id: currentPasswordField; width: parent.width; label: "SENHA ATUAL"; isPassword: true }
                    AuthField { id: newPasswordField; width: parent.width; label: "NOVA SENHA"; isPassword: true }
                    AuthField { id: confirmPasswordField; width: parent.width; label: "CONFIRMAR NOVA SENHA"; isPassword: true }
                    Text {
                        width: parent.width
                        text: "Ao trocar a senha, todas as outras sessões são encerradas."
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    ActionButton {
                        label: "TROCAR SENHA"
                        emphasis: true
                        tint: Theme.blue
                        onClicked: overlay.passwordChangeRequested(
                            currentPasswordField.text, newPasswordField.text, confirmPasswordField.text)
                    }

                    Rectangle { width: parent.width; height: 1; color: Theme.borderSubtle }

                    Text {
                        text: "TWO-FACTOR AUTHENTICATION"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel
                    }
                    Text {
                        width: parent.width
                        text: overlay.twoFactor && overlay.twoFactor.enabled
                            ? "Ativa. Códigos de recuperação restantes: " + overlay.twoFactor.recoveryCodesRemaining
                            : "Inativa. Um segundo fator protege a conta mesmo se a senha vazar."
                        color: overlay.twoFactor && overlay.twoFactor.enabled ? Theme.success : Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    Text {
                        width: parent.width
                        visible: !!(overlay.twoFactor && !overlay.twoFactor.secretProtected)
                        text: "Aviso: este ambiente não tem DPAPI, então o segredo do 2FA fica sem cifra no banco local."
                        color: Theme.warning
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    ActionButton {
                        visible: !(overlay.twoFactor && overlay.twoFactor.enabled)
                        label: "ENABLE 2FA"
                        emphasis: true
                        tint: Theme.violet
                        onClicked: overlay.twoFactorEnrollmentRequested()
                    }

                    Column {
                        width: parent.width
                        spacing: Theme.spacingSm
                        visible: !!(overlay.twoFactor && overlay.twoFactor.enabled)

                        AuthField {
                            id: twoFactorCodeField
                            width: parent.width
                            label: "CÓDIGO DO AUTENTICADOR"
                        }
                        ModalButtonRow {
                            ActionButton {
                                label: "REGENERATE RECOVERY CODES"
                                emphasis: false
                                onClicked: overlay.recoveryRegenerateRequested(twoFactorCodeField.text)
                            }
                            ActionButton {
                                label: "DISABLE 2FA"
                                emphasis: false
                                tint: Theme.danger
                                onClicked: overlay.twoFactorDisableRequested(twoFactorCodeField.text)
                            }
                        }
                    }
                }

                // ----------------------------------------------- SESSIONS
                Column {
                    width: parent.width
                    spacing: Theme.spacingSm
                    visible: overlay._tab === 2

                    Text {
                        width: parent.width
                        text: "Dispositivos com sessão aberta nesta conta. Nenhum token é exibido."
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    Repeater {
                        model: overlay.sessions
                        Rectangle {
                            required property var modelData
                            width: content.width
                            height: sessionColumn.implicitHeight + Theme.spacingSm * 2
                            radius: Theme.radiusSmall
                            color: Theme.surfacePanel
                            border.width: 1
                            border.color: modelData.isCurrent ? Theme.cyan : Theme.borderSubtle

                            Column {
                                id: sessionColumn
                                x: Theme.spacingSm
                                y: Theme.spacingSm
                                width: parent.width - Theme.spacingSm * 2
                                spacing: 3
                                Text {
                                    width: parent.width
                                    text: modelData.device + (modelData.isCurrent ? "  ·  ESTE DISPOSITIVO" : "")
                                    color: modelData.isCurrent ? Theme.cyan : Theme.textPrimary
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: parent.width
                                    text: "Criada em " + modelData.createdAt
                                        + (modelData.lastUsedAt ? "  ·  último uso " + modelData.lastUsedAt : "")
                                    color: Theme.textFaint
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                                // v1.5 — encerrar UMA sessão. Encerrar a atual
                                // desloga na hora (o backend faz o logout); o
                                // rótulo diz isso antes de o usuário clicar.
                                ActionButton {
                                    label: modelData.isCurrent
                                        ? "ENCERRAR ESTA SESSÃO (SAIR)" : "ENCERRAR SESSÃO"
                                    emphasis: false
                                    tint: Theme.danger
                                    onClicked: overlay.sessionRevokeRequested(modelData.id)
                                }
                            }
                        }
                    }

                    ModalButtonRow {
                        ActionButton {
                            label: "LOG OUT OTHER SESSIONS"
                            emphasis: true
                            tint: Theme.blue
                            onClicked: overlay.logOutOthersRequested()
                        }
                        ActionButton {
                            label: "ATUALIZAR"
                            emphasis: false
                            onClicked: overlay.sessionsRefreshRequested()
                        }
                    }
                }

                // ----------------------------------------------- ACTIVITY
                Column {
                    width: parent.width
                    spacing: Theme.spacingSm
                    visible: overlay._tab === 3

                    Text {
                        width: parent.width
                        text: "Eventos de segurança desta conta. Nenhuma senha, token, chave de API "
                            + "ou código de recuperação é registrado aqui."
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    Text {
                        width: parent.width
                        visible: overlay.securityEvents.length === 0
                        text: "Nenhum evento registrado ainda."
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: 12
                    }

                    Repeater {
                        model: overlay.securityEvents
                        Rectangle {
                            required property var modelData
                            width: content.width
                            height: eventColumn.implicitHeight + Theme.spacingSm * 2
                            radius: Theme.radiusSmall
                            color: Theme.surfacePanel
                            border.width: 1
                            border.color: modelData.type === "login_blocked"
                                ? Theme.warning : Theme.borderSubtle

                            Column {
                                id: eventColumn
                                x: Theme.spacingSm
                                y: Theme.spacingSm
                                width: parent.width - Theme.spacingSm * 2
                                spacing: 3
                                Text {
                                    width: parent.width
                                    text: modelData.label
                                    color: modelData.type === "login_blocked"
                                        ? Theme.warning : Theme.textPrimary
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: parent.width
                                    text: modelData.at
                                        + (modelData.detail ? "  ·  " + modelData.detail : "")
                                    color: Theme.textFaint
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                    }

                    ModalButtonRow {
                        ActionButton {
                            label: "ATUALIZAR"
                            emphasis: false
                            onClicked: overlay.securityEventsRefreshRequested()
                        }
                    }
                }

                // -------------------------------------------- AI PROVIDERS
                Column {
                    width: parent.width
                    spacing: Theme.spacingSm
                    visible: overlay._tab === 4

                    Text {
                        width: parent.width
                        text: "Ordem de fallback (somente leitura — definida no .env). Chaves de API "
                            + "nunca são exibidas: apenas se estão presentes ou ausentes."
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    Repeater {
                        model: overlay.providers
                        Rectangle {
                            required property var modelData
                            width: content.width
                            height: providerColumn.implicitHeight + Theme.spacingSm * 2
                            radius: Theme.radiusSmall
                            color: Theme.surfacePanel
                            border.width: 1
                            border.color: modelData.health === "ready" ? Theme.success
                                : modelData.health === "auth_failed" ? Theme.danger
                                : modelData.health === "rate_limited" ? Theme.warning
                                : Theme.borderSubtle

                            Column {
                                id: providerColumn
                                x: Theme.spacingSm
                                y: Theme.spacingSm
                                width: parent.width - Theme.spacingSm * 2
                                spacing: 4

                                Text {
                                    width: parent.width
                                    text: modelData.position + ".  " + modelData.label
                                    color: modelData.enabled ? Theme.textPrimary : Theme.textFaint
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: parent.width
                                    text: modelData.configurationLabel + "  ·  " + modelData.healthLabel
                                    color: modelData.health === "ready" ? Theme.success
                                        : modelData.health === "auth_failed" ? Theme.danger
                                        : Theme.textMuted
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 11
                                    wrapMode: Text.Wrap
                                }
                                Text {
                                    width: parent.width
                                    visible: modelData.models.length > 0
                                    text: modelData.models.join(", ")
                                    color: Theme.textFaint
                                    font.family: Theme.fontFamily
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                                ActionButton {
                                    label: "TEST CONNECTION"
                                    emphasis: false
                                    // Desabilitado quando não há o que testar:
                                    // sem chave ou desativado, a chamada seria
                                    // garantidamente inútil.
                                    enabled: modelData.configured && modelData.enabled
                                        && !overlay.providerTestActive
                                    onClicked: overlay.providerTestRequested(modelData.id)
                                }
                            }
                        }
                    }

                    ModalButtonRow {
                        ActionButton {
                            label: "ATUALIZAR"
                            emphasis: false
                            onClicked: overlay.providersRefreshRequested()
                        }
                    }
                }

                // --------------------------------------- LANGUAGE & REGION
                Column {
                    id: localeTab
                    width: parent.width
                    spacing: Theme.spacingMd
                    visible: overlay._tab === 5

                    // Escolhas pendentes: só viram preferência ao clicar em
                    // SALVAR. Aplicar a cada mexida no seletor reconectaria a
                    // sessão de IA várias vezes enquanto a pessoa ainda está
                    // decidindo.
                    property string pendingLanguage: overlay.locale
                        ? (overlay.locale.languageIsAuto ? "automatic" : overlay.locale.language) : "automatic"
                    property string pendingRegion: overlay.locale
                        ? (overlay.locale.regionIsAuto ? "automatic" : overlay.locale.region) : "automatic"
                    property string pendingCurrency: overlay.locale
                        ? (overlay.locale.currencyIsAuto ? "automatic" : overlay.locale.currency) : "automatic"

                    Text {
                        width: parent.width
                        text: overlay.tr("settings.detected_from", "Detectado da configuração regional do sistema")
                            + (overlay.locale && overlay.locale.detectedLocale
                               ? "  ·  " + overlay.locale.detectedLocale : "")
                        color: Theme.textMuted
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    LocaleSelector {
                        width: parent.width
                        label: overlay.tr("settings.language", "IDIOMA")
                        options: overlay.localeOptions ? overlay.localeOptions.languages : []
                        currentValue: localeTab.pendingLanguage
                        // "Automático — Português (Brasil)": mostra a escolha
                        // E o que ela resolveu, para automático não virar
                        // caixa-preta.
                        resolvedLabel: overlay.locale && overlay.locale.languageIsAuto
                            ? overlay.locale.languageName : ""
                        onSelected: (value) => localeTab.pendingLanguage = value
                    }

                    LocaleSelector {
                        width: parent.width
                        label: overlay.tr("settings.region", "REGIÃO")
                        options: overlay.localeOptions ? overlay.localeOptions.regions : []
                        currentValue: localeTab.pendingRegion
                        resolvedLabel: overlay.locale && overlay.locale.regionIsAuto
                            ? overlay.locale.regionName : ""
                        onSelected: (value) => localeTab.pendingRegion = value
                    }

                    LocaleSelector {
                        width: parent.width
                        label: overlay.tr("settings.currency", "MOEDA")
                        options: overlay.localeOptions ? overlay.localeOptions.currencies : []
                        currentValue: localeTab.pendingCurrency
                        resolvedLabel: overlay.locale && overlay.locale.currencyIsAuto && overlay.locale.currency
                            ? overlay.locale.currency + " (" + overlay.locale.currencySymbol + ")" : ""
                        onSelected: (value) => localeTab.pendingCurrency = value
                    }

                    Text {
                        width: parent.width
                        text: "Idioma e região são independentes: é possível manter as respostas "
                            + "em um idioma e os preços na moeda de outra região."
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }

                    ModalButtonRow {
                        ActionButton {
                            label: overlay.tr("settings.save", "SALVAR")
                            emphasis: true
                            tint: Theme.blue
                            onClicked: overlay.localePreferencesRequested(
                                localeTab.pendingLanguage, localeTab.pendingRegion, localeTab.pendingCurrency)
                        }
                    }
                }

                // -------------------------------------------- DANGER ZONE
                Column {
                    width: parent.width
                    spacing: Theme.spacingMd
                    visible: overlay._tab === 6

                    Rectangle {
                        width: parent.width
                        height: dangerText.implicitHeight + Theme.spacingSm * 2
                        radius: Theme.radiusSmall
                        color: Qt.rgba(1, 0.42, 0.5, 0.10)
                        border.width: 1
                        border.color: Theme.danger
                        Text {
                            id: dangerText
                            x: Theme.spacingSm
                            y: Theme.spacingSm
                            width: parent.width - Theme.spacingSm * 2
                            text: "Apagar a conta remove permanentemente seus chats, mensagens, memória, "
                                + "sessões, configurações e a verificação em duas etapas. Isso NÃO pode ser desfeito. "
                                + "Outras contas neste computador não são afetadas."
                            color: Theme.danger
                            font.family: Theme.fontFamily
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                    }

                    AuthField { id: deletePasswordField; width: parent.width; label: "SENHA ATUAL"; isPassword: true }
                    AuthField {
                        id: deleteTwoFactorField
                        width: parent.width
                        visible: !!(overlay.user && overlay.user.twoFactorEnabled)
                        label: "CÓDIGO DO AUTENTICADOR"
                    }
                    AuthField { id: deleteConfirmField; width: parent.width; label: "DIGITE DELETE PARA CONFIRMAR" }

                    ActionButton {
                        label: "DELETE ACCOUNT"
                        emphasis: true
                        tint: Theme.danger
                        enabled: deleteConfirmField.text.toUpperCase() === "DELETE"
                        onClicked: overlay.deleteAccountRequested(
                            deletePasswordField.text, deleteConfirmField.text, deleteTwoFactorField.text)
                    }
                }

                // --- Mensagens ---
                Text {
                    width: parent.width
                    visible: overlay.errorText.length > 0
                    text: overlay.errorText
                    color: Theme.danger
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                Text {
                    width: parent.width
                    visible: overlay.successText.length > 0
                    text: overlay.successText
                    color: Theme.success
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }

                ModalButtonRow {
                    ActionButton {
                        label: "FECHAR"
                        emphasis: false
                        onClicked: overlay.closeRequested()
                    }
                }
            }
        }
    }
}
