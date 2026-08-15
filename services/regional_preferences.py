"""Idioma, região, moeda e fuso — fonte ÚNICA de preferência regional
(v1.6.0, Partes E e F).

--------------------------------------------------------------------------
Por que uma estrutura só
--------------------------------------------------------------------------
Sem isto, "o usuário é brasileiro" viraria `if country == "BR"` espalhado
por prompt, formatação de preço, escolha de voz do TTS e formato de data —
quatro lugares para divergir e nenhum para consertar. `RegionalPreferences`
é o objeto que todos consultam.

--------------------------------------------------------------------------
Idioma e região são INDEPENDENTES
--------------------------------------------------------------------------
Deliberadamente separados, porque a combinação cruzada é comum e legítima:
um brasileiro morando nos EUA quer resposta em português e preço em dólar;
um americano no Brasil quer o inverso. Derivar região do idioma (ou o
contrário) quebraria os dois casos. `pt-BR` é uma dica FORTE de Brasil, não
uma prova — e é assim que o código a trata.

--------------------------------------------------------------------------
Privacidade: detecção é 100% local
--------------------------------------------------------------------------
A região vem da configuração regional do próprio sistema operacional. Nunca
GPS, nunca endereço, nunca varredura de Wi-Fi, nunca fingerprinting, e
nunca o IP enviado a um serviço externo de geolocalização — descobrir o país
não justifica entregar o endereço de rede do usuário a um terceiro.

O que é persistido é a ESCOLHA (`automatic` ou um código), nunca uma
localização. `automatic` é gravado como `automatic`, e a detecção roda de
novo a cada sessão — assim viajar ou trocar a configuração do Windows é
refletido sem precisar mexer em nada.

--------------------------------------------------------------------------
Precedência (item 68)
--------------------------------------------------------------------------
1. override explícito do usuário
2. configuração regional do SO
3. locale
4. fuso horário — apenas como pista auxiliar
5. default seguro do app

O fuso nunca vence um override manual: um notebook configurado em
`America/Sao_Paulo` pode pertencer a alguém que escolheu explicitamente
região Portugal, e a escolha da pessoa ganha sempre.
"""

import locale as _locale
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

logger = logging.getLogger(__name__)

AUTOMATIC = "automatic"


class Language(str, Enum):
    """Idiomas com suporte real nesta versão. Extensível: acrescentar um
    membro aqui e a entrada correspondente nos mapas abaixo é tudo que um
    idioma novo exige — nenhuma lógica nova."""

    PT_BR = "pt-BR"
    EN_US = "en-US"
    ES = "es"


LANGUAGE_DISPLAY_NAMES: dict[Language, str] = {
    Language.PT_BR: "Português (Brasil)",
    Language.EN_US: "English (US)",
    Language.ES: "Español",
}

# Nome do idioma EM INGLÊS, para o prompt enviado ao provider. Os modelos
# reconhecem "Portuguese (Brazil)" de forma muito mais confiável do que
# "Português (Brasil)" como instrução — o rótulo em inglês é técnico, e
# nada tem a ver com o idioma da resposta.
LANGUAGE_PROMPT_NAMES: dict[Language, str] = {
    Language.PT_BR: "Portuguese (Brazil)",
    Language.EN_US: "English (United States)",
    Language.ES: "Spanish",
}

DEFAULT_LANGUAGE = Language.PT_BR


# ----------------------------------------------------------------------
# Regiões
# ----------------------------------------------------------------------
# Subconjunto explícito, com código ISO 3166-1 alfa-2. Não é uma tabela de
# todos os países do mundo: só entram regiões cuja moeda e formatação este
# código realmente sabe tratar. Uma região fora daqui cai no default seguro
# em vez de produzir formatação errada com ar de certeza.
REGION_DISPLAY_NAMES: dict[str, str] = {
    "BR": "Brasil",
    "US": "Estados Unidos",
    "GB": "Reino Unido",
    "PT": "Portugal",
    "ES": "Espanha",
    "FR": "França",
    "DE": "Alemanha",
    "IT": "Itália",
    "IE": "Irlanda",
    "NL": "Países Baixos",
    "JP": "Japão",
    "CA": "Canadá",
    "AU": "Austrália",
    "CH": "Suíça",
    "MX": "México",
    "AR": "Argentina",
}

DEFAULT_REGION = "BR"

# Zona do euro: um mapa país -> EUR seria repetitivo e fácil de desatualizar
# pela metade; o conjunto deixa a regra explícita.
EUROZONE = frozenset({"PT", "ES", "FR", "DE", "IT", "IE", "NL", "AT", "BE", "FI", "GR", "LU"})

REGION_CURRENCIES: dict[str, str] = {
    "BR": "BRL",
    "US": "USD",
    "GB": "GBP",
    "JP": "JPY",
    "CA": "CAD",
    "AU": "AUD",
    "CH": "CHF",
    "MX": "MXN",
    "AR": "ARS",
}

DEFAULT_CURRENCY = "BRL"


@dataclass(frozen=True)
class CurrencyFormat:
    """Como uma moeda é escrita. `symbol_first` e os separadores são o que
    diferencia `R$ 1.499,90` de `$1,499.90` — montar isso por concatenação
    ad-hoc é como se produz `R$1,499.90`, que não é de nenhum dos dois."""

    code: str
    symbol: str
    symbol_first: bool = True
    thousands: str = "."
    decimal: str = ","
    space_after_symbol: bool = True


CURRENCY_FORMATS: dict[str, CurrencyFormat] = {
    "BRL": CurrencyFormat("BRL", "R$", True, ".", ",", True),
    "USD": CurrencyFormat("USD", "$", True, ",", ".", False),
    "GBP": CurrencyFormat("GBP", "£", True, ",", ".", False),
    "EUR": CurrencyFormat("EUR", "€", False, ".", ",", True),
    # O iene não usa casas decimais na prática; `decimals=0` é resolvido em
    # `format_currency` a partir do próprio código da moeda.
    "JPY": CurrencyFormat("JPY", "¥", True, ",", ".", False),
    "CAD": CurrencyFormat("CAD", "CA$", True, ",", ".", False),
    "AUD": CurrencyFormat("AUD", "A$", True, ",", ".", False),
    "CHF": CurrencyFormat("CHF", "CHF", True, "'", ".", True),
    "MXN": CurrencyFormat("MXN", "MX$", True, ",", ".", False),
    "ARS": CurrencyFormat("ARS", "AR$", True, ".", ",", True),
}

_ZERO_DECIMAL_CURRENCIES = frozenset({"JPY"})

# Formato de data por região. `%d/%m/%Y` vs `%m/%d/%Y` é a diferença que faz
# 08/15 ser inválido no Brasil e 15/08 ser inválido nos EUA.
_DATE_FORMATS: dict[str, str] = {"US": "%m/%d/%Y"}
_DEFAULT_DATE_FORMAT = "%d/%m/%Y"


# ----------------------------------------------------------------------
# Detecção (somente local)
# ----------------------------------------------------------------------


def detect_system_locale() -> str:
    """Locale do SO no formato `ll-CC` (ex.: `pt-BR`), ou string vazia.

    No Windows pergunta direto à API (`GetUserDefaultLocaleName`), que é a
    configuração regional que a pessoa realmente escolheu no painel — o
    `locale` do Python depende de `setlocale` e frequentemente devolve `C`
    num processo que nunca o chamou. As variáveis de ambiente cobrem
    Linux/macOS e servem de fallback."""
    if os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(85)  # LOCALE_NAME_MAX_LENGTH
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
                return buffer.value.replace("_", "-")
        except Exception:
            logger.debug("GetUserDefaultLocaleName indisponível; usando fallback de locale.")

    for variable in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        raw = os.environ.get(variable, "")
        if raw and raw not in ("C", "POSIX"):
            return raw.split(".")[0].split(":")[0].replace("_", "-")

    try:
        language, _encoding = _locale.getlocale()
        if language and language not in ("C", "POSIX"):
            return language.replace("_", "-")
    except (ValueError, TypeError):
        pass
    return ""


def detect_system_timezone() -> str:
    """Nome do fuso do sistema. Pista AUXILIAR — nunca decide região sozinho
    quando existe locale ou override (item 67)."""
    try:
        local_tz = datetime.now().astimezone().tzinfo
        name = str(local_tz) if local_tz else ""
        if name and name not in ("UTC",):
            return name
        return time.tzname[0] if time.tzname else name
    except Exception:
        return ""


# Pista fraca: só consultada quando o locale não informou região nenhuma.
_TIMEZONE_REGION_HINTS: dict[str, str] = {
    "america/sao_paulo": "BR",
    "america/fortaleza": "BR",
    "america/manaus": "BR",
    "e. south america standard time": "BR",
    "america/new_york": "US",
    "america/chicago": "US",
    "america/los_angeles": "US",
    "eastern standard time": "US",
    "pacific standard time": "US",
    "europe/london": "GB",
    "gmt standard time": "GB",
    "europe/lisbon": "PT",
    "europe/madrid": "ES",
    "europe/paris": "FR",
    "europe/berlin": "DE",
    "asia/tokyo": "JP",
}


def _region_from_timezone(timezone_name: str) -> str:
    return _TIMEZONE_REGION_HINTS.get((timezone_name or "").strip().lower(), "")


def language_from_locale(locale_tag: str) -> Language | None:
    """`pt-BR` -> `PT_BR`. Casa primeiro o par completo e depois só o idioma,
    para `es-MX`, `es-AR` e `es-ES` chegarem todos a Espanhol sem precisarem
    de entrada própria."""
    tag = (locale_tag or "").strip().replace("_", "-")
    if not tag:
        return None
    lowered = tag.lower()
    for language in Language:
        if lowered == language.value.lower():
            return language
    primary = lowered.split("-")[0]
    if primary == "pt":
        return Language.PT_BR
    if primary == "en":
        return Language.EN_US
    if primary == "es":
        return Language.ES
    return None


def region_from_locale(locale_tag: str) -> str:
    """Extrai o código de país de `ll-CC`. Devolve vazio quando o locale não
    traz região (`pt` sozinho) — inventar "provavelmente Brasil" a partir do
    idioma é exatamente o acoplamento que este módulo evita."""
    tag = (locale_tag or "").strip().replace("_", "-")
    parts = [part for part in tag.split("-") if part]
    if len(parts) < 2:
        return ""
    candidate = parts[-1].upper()
    return candidate if len(candidate) == 2 and candidate.isalpha() else ""


def currency_for_region(region: str) -> str:
    """Moeda oficial da região. A zona do euro é resolvida pelo conjunto, e
    uma região desconhecida cai no default em vez de inventar um código."""
    region = (region or "").upper()
    if region in EUROZONE:
        return "EUR"
    return REGION_CURRENCIES.get(region, DEFAULT_CURRENCY)


# ----------------------------------------------------------------------
# Preferências resolvidas
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class RegionalPreferences:
    """Preferências JÁ resolvidas — nunca `automatic`. Quem consome (prompt,
    formatação, TTS, UI) sempre recebe um valor concreto; os três flags
    `*_is_auto` existem só para a tela poder mostrar "Automatic — Brasil" em
    vez de esconder de onde veio."""

    language: Language = DEFAULT_LANGUAGE
    locale: str = ""
    region: str = DEFAULT_REGION
    currency: str = DEFAULT_CURRENCY
    timezone: str = ""
    language_is_auto: bool = True
    region_is_auto: bool = True
    currency_is_auto: bool = True

    @property
    def language_display_name(self) -> str:
        return LANGUAGE_DISPLAY_NAMES.get(self.language, self.language.value)

    @property
    def language_prompt_name(self) -> str:
        return LANGUAGE_PROMPT_NAMES.get(self.language, self.language.value)

    @property
    def region_display_name(self) -> str:
        return REGION_DISPLAY_NAMES.get(self.region, self.region)

    @property
    def currency_symbol(self) -> str:
        return CURRENCY_FORMATS.get(self.currency, CURRENCY_FORMATS[DEFAULT_CURRENCY]).symbol

    @property
    def tts_locale(self) -> str:
        """Etiqueta de locale para escolher a voz do TTS. Espanhol não tem
        região definida no enum, então recebe uma padrão — sem isto, o
        seletor de voz não teria por onde procurar."""
        return "es-ES" if self.language is Language.ES else self.language.value


def resolve_preferences(
    *,
    language_choice: str = AUTOMATIC,
    region_choice: str = AUTOMATIC,
    currency_choice: str = AUTOMATIC,
    system_locale: str | None = None,
    system_timezone: str | None = None,
) -> RegionalPreferences:
    """Aplica a precedência do item 68 e devolve preferências concretas.

    `system_locale`/`system_timezone` são injetáveis para os testes exercerem
    qualquer configuração regional sem depender da máquina que roda a suíte —
    nunca para produção passar um valor de fora."""
    detected_locale = system_locale if system_locale is not None else detect_system_locale()
    detected_timezone = system_timezone if system_timezone is not None else detect_system_timezone()

    # --- Idioma ---
    language_is_auto = _is_automatic(language_choice)
    if language_is_auto:
        language = language_from_locale(detected_locale) or DEFAULT_LANGUAGE
    else:
        language = _coerce_language(language_choice)

    # --- Região (independente do idioma) ---
    region_is_auto = _is_automatic(region_choice)
    if region_is_auto:
        region = region_from_locale(detected_locale)
        if not region:
            # Só agora o fuso entra, e só como pista.
            region = _region_from_timezone(detected_timezone)
        if region not in REGION_DISPLAY_NAMES:
            region = DEFAULT_REGION
    else:
        candidate = (region_choice or "").upper()
        region = candidate if candidate in REGION_DISPLAY_NAMES else DEFAULT_REGION

    # --- Moeda (derivada da região quando automática) ---
    currency_is_auto = _is_automatic(currency_choice)
    if currency_is_auto:
        currency = currency_for_region(region)
    else:
        candidate = (currency_choice or "").upper()
        currency = candidate if candidate in CURRENCY_FORMATS else currency_for_region(region)

    return RegionalPreferences(
        language=language,
        locale=detected_locale,
        region=region,
        currency=currency,
        timezone=detected_timezone,
        language_is_auto=language_is_auto,
        region_is_auto=region_is_auto,
        currency_is_auto=currency_is_auto,
    )


def _is_automatic(choice: str | None) -> bool:
    return not choice or str(choice).strip().lower() == AUTOMATIC


def _coerce_language(choice: str) -> Language:
    raw = (choice or "").strip()
    for language in Language:
        if raw.lower() == language.value.lower():
            return language
    return language_from_locale(raw) or DEFAULT_LANGUAGE


# ----------------------------------------------------------------------
# Formatação
# ----------------------------------------------------------------------


def format_number(value: float, preferences: RegionalPreferences, *, decimals: int = 2) -> str:
    """Número no formato da moeda/região vigente (`1.500,50` vs `1,500.50`).

    Só para valores que o APP formata para exibição. Nunca aplicar a versão,
    endereço IP, identificador de modelo, path ou qualquer dado técnico —
    trocar o separador ali corromperia o valor."""
    fmt = CURRENCY_FORMATS.get(preferences.currency, CURRENCY_FORMATS[DEFAULT_CURRENCY])
    # Formata com separadores neutros e troca depois, via marcador
    # intermediário: substituir "." por "," e "," por "." em sequência
    # transformaria tudo no mesmo caractere.
    base = f"{value:,.{decimals}f}"
    return base.replace(",", "\x00").replace(".", fmt.decimal).replace("\x00", fmt.thousands)


def format_currency(value: float, preferences: RegionalPreferences) -> str:
    """`R$ 1.499,90`, `$1,499.90`, `1.499,90 €` — símbolo, posição e
    separadores todos vindos da moeda vigente."""
    fmt = CURRENCY_FORMATS.get(preferences.currency, CURRENCY_FORMATS[DEFAULT_CURRENCY])
    decimals = 0 if fmt.code in _ZERO_DECIMAL_CURRENCIES else 2
    number = format_number(value, preferences, decimals=decimals)
    separator = " " if fmt.space_after_symbol else ""
    if fmt.symbol_first:
        return f"{fmt.symbol}{separator}{number}"
    return f"{number}{separator}{fmt.symbol}"


def format_date(value: date | datetime, preferences: RegionalPreferences) -> str:
    """Data no formato da região. Apenas APRESENTAÇÃO: o armazenamento
    continua em ISO/UTC como sempre (ver `services/conversation_repository.py`)."""
    pattern = _DATE_FORMATS.get(preferences.region, _DEFAULT_DATE_FORMAT)
    return value.strftime(pattern)
