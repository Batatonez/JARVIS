"""Tradução da interface e das mensagens user-facing (v1.6.0, Parte E).

--------------------------------------------------------------------------
O que este módulo resolve
--------------------------------------------------------------------------
O JARVIS nasceu com texto em português cravado em QML e em mensagens de
erro. Traduzir isso com `if idioma == "pt"` dentro de cada arquivo de
interface produziria dezenas de pontos de decisão divergentes — e o primeiro
que ficasse desatualizado mostraria a mistura de idiomas que a v1.6.0
justamente veio corrigir.

Aqui existe UM catálogo por idioma e UMA função de busca. O QML recebe texto
já traduzido através do Bridge; nenhum arquivo de interface decide idioma.

--------------------------------------------------------------------------
Escopo honesto desta versão
--------------------------------------------------------------------------
A infraestrutura é completa e extensível, mas o catálogo cobre os fluxos
principais (Settings, Language & Region, conta, status de provider, erros
user-facing) — não cada string histórica do aplicativo. Traduzir tudo de uma
vez exigiria varrer todo o QML acumulado desde a v0.5, e o valor disso é
menor que o risco de quebrar telas que hoje funcionam.

Consequência assumida: telas ainda não catalogadas continuam em português
mesmo com outro idioma selecionado. Isso está registrado como limitação, e
não é apresentado como concluído.

--------------------------------------------------------------------------
Chave ausente
--------------------------------------------------------------------------
`translate()` cai para o idioma padrão e, se nem lá existir, devolve a
PRÓPRIA CHAVE. Nunca string vazia: um botão sem rótulo é um bug invisível,
enquanto uma chave crua aparecendo na tela é imediatamente diagnosticável.
"""

import logging

from services.regional_preferences import DEFAULT_LANGUAGE, Language

logger = logging.getLogger(__name__)

# Catálogos. Chave estável em inglês, em `área.item`, para uma tradução nova
# não depender de conhecer o texto original em português.
_CATALOGS: dict[Language, dict[str, str]] = {
    Language.PT_BR: {
        # --- Settings: Language & Region ---
        # --- Activity Trace (camada de precisão) ---
        # Descrevem EXECUÇÃO, nunca pensamento: "Pesquisando na web" só
        # aparece se uma busca realmente rodou.
        "activity.thinking": "Pensando",
        "activity.interpreting": "Interpretando a pergunta",
        "activity.clarifying": "Pedindo esclarecimento",
        "activity.searching_web": "Pesquisando na web",
        "activity.reading_source": "Lendo fonte",
        "activity.searching_files": "Procurando arquivos",
        "activity.running_tool": "Executando ferramenta",
        "activity.comparing": "Comparando informações",
        "activity.verifying": "Verificando fontes",
        "activity.drafting": "Redigindo",
        "activity.responding": "Respondendo",
        "activity.completed": "Concluído",
        "activity.error": "Falhou",
        "sources.title": "FONTES",
        "sources.button": "Fontes",
        "sources.none": "Nenhuma fonte foi consultada.",
        "accuracy.unverified": "Não consegui verificar isso agora.",
        "settings.language_region": "IDIOMA E REGIÃO",
        "settings.language": "IDIOMA",
        "settings.region": "REGIÃO",
        "settings.currency": "MOEDA",
        "settings.automatic": "Automático",
        "settings.detected_from": "Detectado da configuração regional do sistema",
        "settings.save": "SALVAR",
        "settings.close": "FECHAR",
        "settings.refresh": "ATUALIZAR",
        # --- Conta ---
        "account.title": "CONTA",
        "account.profile": "PERFIL",
        "account.security": "SEGURANÇA",
        "account.sessions": "SESSÕES",
        "account.activity": "ATIVIDADE",
        "account.providers": "PROVIDERS DE IA",
        "account.danger_zone": "ZONA DE PERIGO",
        # --- Status de provider ---
        "provider.ready": "Pronto",
        "provider.rate_limited": "Limite de uso atingido",
        "provider.unavailable": "Indisponível no momento",
        "provider.auth_failed": "Falha de autenticação",
        "provider.disabled": "Desativado",
        "provider.not_configured": "Não configurado",
        "provider.unknown": "Não testado",
        "provider.configured": "Configurado ✓",
        "provider.key_missing": "Chave ausente",
        "provider.test_connection": "TESTAR CONEXÃO",
        # --- Erros user-facing ---
        "error.ai_unavailable": "A IA está temporariamente indisponível.",
        "error.provider_refused": "Não posso ajudar com esse pedido.",
        "error.rate_limited": "Muitas requisições. Tente novamente em instantes.",
        "error.no_provider": "Nenhum provider de IA está configurado.",
        "error.internal": "Ocorreu um erro interno inesperado.",
    },
    Language.EN_US: {
        "activity.thinking": "Thinking",
        "activity.interpreting": "Interpreting the question",
        "activity.clarifying": "Asking for clarification",
        "activity.searching_web": "Searching the web",
        "activity.reading_source": "Reading source",
        "activity.searching_files": "Searching files",
        "activity.running_tool": "Running tool",
        "activity.comparing": "Comparing information",
        "activity.verifying": "Verifying sources",
        "activity.drafting": "Drafting",
        "activity.responding": "Responding",
        "activity.completed": "Done",
        "activity.error": "Failed",
        "sources.title": "SOURCES",
        "sources.button": "Sources",
        "sources.none": "No sources were consulted.",
        "accuracy.unverified": "I could not verify this right now.",
        "settings.language_region": "LANGUAGE & REGION",
        "settings.language": "LANGUAGE",
        "settings.region": "REGION",
        "settings.currency": "CURRENCY",
        "settings.automatic": "Automatic",
        "settings.detected_from": "Detected from system regional settings",
        "settings.save": "SAVE",
        "settings.close": "CLOSE",
        "settings.refresh": "REFRESH",
        "account.title": "ACCOUNT",
        "account.profile": "PROFILE",
        "account.security": "SECURITY",
        "account.sessions": "SESSIONS",
        "account.activity": "ACTIVITY",
        "account.providers": "AI PROVIDERS",
        "account.danger_zone": "DANGER ZONE",
        "provider.ready": "Ready",
        "provider.rate_limited": "Rate limited",
        "provider.unavailable": "Currently unavailable",
        "provider.auth_failed": "Authentication failed",
        "provider.disabled": "Disabled",
        "provider.not_configured": "Not configured",
        "provider.unknown": "Not tested",
        "provider.configured": "Configured ✓",
        "provider.key_missing": "Key missing",
        "provider.test_connection": "TEST CONNECTION",
        "error.ai_unavailable": "AI is temporarily unavailable.",
        "error.provider_refused": "I can't help with that request.",
        "error.rate_limited": "Too many requests. Please try again shortly.",
        "error.no_provider": "No AI provider is configured.",
        "error.internal": "An unexpected internal error occurred.",
    },
    Language.ES: {
        "activity.thinking": "Pensando",
        "activity.interpreting": "Interpretando la pregunta",
        "activity.clarifying": "Pidiendo aclaración",
        "activity.searching_web": "Buscando en la web",
        "activity.reading_source": "Leyendo fuente",
        "activity.searching_files": "Buscando archivos",
        "activity.running_tool": "Ejecutando herramienta",
        "activity.comparing": "Comparando información",
        "activity.verifying": "Verificando fuentes",
        "activity.drafting": "Redactando",
        "activity.responding": "Respondiendo",
        "activity.completed": "Completado",
        "activity.error": "Falló",
        "sources.title": "FUENTES",
        "sources.button": "Fuentes",
        "sources.none": "No se consultó ninguna fuente.",
        "accuracy.unverified": "No pude verificar esto ahora.",
        "settings.language_region": "IDIOMA Y REGIÓN",
        "settings.language": "IDIOMA",
        "settings.region": "REGIÓN",
        "settings.currency": "MONEDA",
        "settings.automatic": "Automático",
        "settings.detected_from": "Detectado de la configuración regional del sistema",
        "settings.save": "GUARDAR",
        "settings.close": "CERRAR",
        "settings.refresh": "ACTUALIZAR",
        "account.title": "CUENTA",
        "account.profile": "PERFIL",
        "account.security": "SEGURIDAD",
        "account.sessions": "SESIONES",
        "account.activity": "ACTIVIDAD",
        "account.providers": "PROVEEDORES DE IA",
        "account.danger_zone": "ZONA DE PELIGRO",
        "provider.ready": "Listo",
        "provider.rate_limited": "Límite de uso alcanzado",
        "provider.unavailable": "No disponible ahora",
        "provider.auth_failed": "Fallo de autenticación",
        "provider.disabled": "Desactivado",
        "provider.not_configured": "No configurado",
        "provider.unknown": "Sin probar",
        "provider.configured": "Configurado ✓",
        "provider.key_missing": "Falta la clave",
        "provider.test_connection": "PROBAR CONEXIÓN",
        "error.ai_unavailable": "La IA no está disponible temporalmente.",
        "error.provider_refused": "No puedo ayudar con esa solicitud.",
        "error.rate_limited": "Demasiadas solicitudes. Inténtalo de nuevo en unos instantes.",
        "error.no_provider": "No hay ningún proveedor de IA configurado.",
        "error.internal": "Ocurrió un error interno inesperado.",
    },
}


def translate(key: str, language: Language = DEFAULT_LANGUAGE) -> str:
    """Texto traduzido de `key`. Cai para o idioma padrão e, em último caso,
    devolve a própria chave — nunca string vazia."""
    catalog = _CATALOGS.get(language) or {}
    if key in catalog:
        return catalog[key]
    fallback = _CATALOGS.get(DEFAULT_LANGUAGE) or {}
    if key in fallback:
        logger.debug("Chave '%s' sem tradução em %s; usando o idioma padrão.", key, language.value)
        return fallback[key]
    logger.warning("Chave de tradução desconhecida: %s", key)
    return key


def catalog_for(language: Language) -> dict[str, str]:
    """Catálogo COMPLETO do idioma, já com as chaves faltantes preenchidas
    pelo padrão. É isto que o Bridge entrega ao QML de uma vez, para a
    interface nunca precisar chamar de volta por string."""
    merged = dict(_CATALOGS.get(DEFAULT_LANGUAGE) or {})
    merged.update(_CATALOGS.get(language) or {})
    return merged


def available_languages() -> tuple[Language, ...]:
    return tuple(_CATALOGS.keys())
