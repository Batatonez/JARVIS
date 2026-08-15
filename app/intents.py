"""Roteamento de intenção — o que o usuário quer que aconteça (v1.7).

--------------------------------------------------------------------------
Por que não mandar tudo para o modelo
--------------------------------------------------------------------------
"abrir Spotify" tem uma resposta certa, imediata e local. Mandá-la para um
provider de IA custa uma chamada de rede, adiciona um segundo de latência,
depende de a internet estar de pé e pode falhar de dez formas diferentes —
para uma operação que o sistema operacional resolve em milissegundos.

Pior: um modelo pode "responder" a `abrir Spotify` com texto explicando como
abrir o Spotify, o que não é o que a pessoa pediu.

Então o roteamento é determinístico primeiro. O modelo é o destino de tudo
que NÃO é um comando claro — que continua sendo a maioria das mensagens, e é
onde ele é insubstituível.

--------------------------------------------------------------------------
A regra de segurança que estrutura este módulo
--------------------------------------------------------------------------
Entender a intenção e EXECUTAR a ação são coisas separadas, e este módulo só
faz a primeira. `route()` devolve uma `ActionRequest` descrevendo o que foi
entendido; quem decide se aquilo pode acontecer — permissão, nível de risco,
confirmação — é `app/actions.py`.

Isso importa porque significa que nenhum caminho de interpretação, inclusive
um que passe pelo modelo no futuro, ganha autoridade de execução por ter
"entendido" algo. A autorização é sempre uma segunda decisão, tomada por
outro objeto, com base no tipo de ação e não em quem a propôs.

--------------------------------------------------------------------------
Ambiguidade
--------------------------------------------------------------------------
Na dúvida, `CHAT`. Um comando não reconhecido virar conversa é inofensivo (a
IA responde); uma conversa virar comando por engano abre um aplicativo, mexe
no volume ou tira um print que ninguém pediu. O custo dos dois erros não é
simétrico, e o padrão segue o lado barato.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class Intent(Enum):
    """O que a entrada do usuário representa."""

    CHAT = "chat"
    OPEN_APP = "open_app"
    SYSTEM_ACTION = "system_action"
    FILE_SEARCH = "file_search"
    REMINDER = "reminder"
    TOOL = "tool"
    SETTINGS = "settings"
    NAVIGATION = "navigation"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RoutedIntent:
    """Resultado do roteamento: o que foi entendido, com que confiança e com
    que parâmetros.

    `confidence` não é probabilidade de modelo — é o quanto o casamento foi
    literal. `1.0` significa "a frase casou com um padrão explícito de
    comando"; valores menores existem para a UI poder pedir confirmação em
    vez de agir. `raw_text` é preservado porque o destino `CHAT` precisa da
    mensagem original, sem nenhuma normalização."""

    intent: Intent
    raw_text: str
    parameters: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0

    @property
    def is_chat(self) -> bool:
        return self.intent is Intent.CHAT


def _normalize(text: str) -> str:
    """Minúsculas, sem acento e sem espaço duplicado — só para CASAR padrão.

    A remoção de acento existe porque "vídeo"/"video" e "está"/"esta" são a
    mesma intenção digitada com pressa, e um comando não pode falhar por
    causa disso. O texto ORIGINAL nunca é alterado: `RoutedIntent.raw_text`
    carrega o que a pessoa escreveu, com acento e caixa."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.lower().split())


# --------------------------------------------------------------------------
# Padrões
# --------------------------------------------------------------------------
# Cada padrão precisa de um VERBO de comando. Sem essa exigência, "spotify"
# sozinho abriria o aplicativo — e "o spotify travou de novo" é uma frase de
# conversa, não um pedido para abrir nada.

_OPEN_APP = re.compile(
    r"^(?:abrir?|abre|inicia(?:r)?|executa(?:r)?|roda(?:r)?|lanca(?:r)?|open|launch|start)\s+"
    r"(?:o\s+|a\s+|the\s+)?(?P<target>.+)$"
)

_CLOSE_APP = re.compile(
    r"^(?:fecha(?:r)?|encerra(?:r)?|mata(?:r)?|close|quit|kill)\s+"
    r"(?:o\s+|a\s+|the\s+)?(?P<target>.+)$"
)

# Volume: aceita "aumentar volume", "volume 50", "diminui o volume", "mudo".
_VOLUME_SET = re.compile(r"^(?:volume|som)\s+(?:para\s+|pra\s+|to\s+)?(?P<value>\d{1,3})%?$")
_VOLUME_UP = re.compile(r"^(?:aumenta(?:r)?|sobe(?:r)?|subir|raise|increase)\s+(?:o\s+)?(?:volume|som)$")
_VOLUME_DOWN = re.compile(r"^(?:diminui(?:r)?|abaixa(?:r)?|baixa(?:r)?|lower|decrease)\s+(?:o\s+)?(?:volume|som)$")
_MUTE = re.compile(r"^(?:mudo|muta(?:r)?|silencia(?:r)?|mute)(?:\s+(?:o\s+)?(?:volume|som|audio))?$")
_UNMUTE = re.compile(r"^(?:desmuta(?:r)?|tira(?:r)?\s+(?:o\s+)?mudo|unmute)(?:\s+(?:o\s+)?(?:volume|som|audio))?$")

_SCREENSHOT = re.compile(
    r"^(?:tira(?:r)?\s+(?:um\s+)?(?:print|screenshot|captura(?:\s+de\s+tela)?)"
    r"|print(?:\s+da\s+tela)?|screenshot|captura(?:r)?\s+(?:a\s+)?tela)$"
)

_CLIPBOARD_READ = re.compile(
    r"^(?:o\s+que\s+(?:eu\s+)?copiei|ler?\s+(?:o\s+)?clipboard|"
    r"(?:mostra(?:r)?|ver)\s+(?:a\s+)?area\s+de\s+transferencia|read\s+clipboard)\??$"
)
_CLIPBOARD_SUMMARIZE = re.compile(
    r"^(?:resum(?:e|ir)|summarize)\s+(?:o\s+)?(?:meu\s+)?(?:clipboard|area\s+de\s+transferencia)$"
)

_PROCESSES = re.compile(
    r"^(?:quais\s+(?:apps?|programas?|processos?).*(?:memoria|ram|cpu)"
    r"|(?:lista(?:r)?|mostra(?:r)?)\s+(?:os\s+)?processos?"
    r"|processos?\s+(?:abertos|rodando)"
    r"|(?:top|maiores)\s+processos?)\??$"
)

_SYSTEM_INFO = re.compile(
    r"^(?:info(?:rmacoes)?\s+do\s+sistema|system\s+info|specs?\s+(?:do|da)\s+(?:pc|maquina)"
    r"|quanto(?:s)?\s+(?:de\s+)?(?:ram|memoria)\s+(?:eu\s+)?tenho)\??$"
)

_OPEN_FOLDER = re.compile(
    r"^(?:abrir?|abre|open)\s+(?:a\s+)?(?:pasta|folder|diretorio)\s+(?P<target>.+)$"
)

_OPEN_URL = re.compile(r"^(?:abrir?|abre|open)\s+(?P<url>https?://\S+)$")

_FILE_SEARCH = re.compile(
    r"^(?:acha(?:r)?|encontra(?:r)?|procura(?:r)?|busca(?:r)?|find|search)\s+"
    r"(?:o\s+|a\s+|meu\s+|minha\s+|the\s+|my\s+)?(?P<query>.+)$"
)
_FILE_WHERE = re.compile(r"^onde\s+(?:esta|fica)\s+(?:o\s+|a\s+|aquele\s+|aquela\s+)?(?P<query>.+?)\??$")

_REMINDER = re.compile(
    r"^(?:me\s+)?(?:lembra(?:r)?|lembre|relembra(?:r)?|remind)\s+(?:me\s+)?(?P<what>.+)$"
)

_SETTINGS = re.compile(
    r"^(?:abrir?|abre|open)?\s*(?:as\s+)?(?:configuracoes|settings|preferencias|ajustes)$"
)

# Cálculo: só expressão aritmética pura ou porcentagem explícita. Deliberado —
# "quanto é 2+2" é comando; "me explica por que 2+2 é 4" é conversa.
# "quanto é" e "qual é" são igualmente comuns em português para pedir uma
# conta, e "what is" aparece quando a pessoa digita em inglês por hábito.
_ASK_PREFIX = r"(?:(?:quanto|qual|quantos?)\s+(?:e|eh|sao|da)\s+|what\s+is\s+|calcula(?:r)?\s+|=)?"
_MATH = re.compile(rf"^{_ASK_PREFIX}(?P<expr>[\d\s().+\-*/^%]+)$")
_PERCENT_OF = re.compile(
    rf"^{_ASK_PREFIX}(?P<percent>\d+(?:[.,]\d+)?)\s*%\s+de\s+(?P<value>\d+(?:[.,]\d+)?)\??$"
)


class IntentRouter:
    """Classifica a entrada do usuário. Puro: não executa nada, não toca o
    sistema, não chama a IA."""

    def route(self, text: str) -> RoutedIntent:
        raw = (text or "").strip()
        if not raw:
            return RoutedIntent(Intent.UNKNOWN, raw, confidence=0.0)

        # Comandos de barra (`/status`, `/new`) continuam sendo do
        # `CommandRegistry` desde a v0.3 — nunca reinterpretados aqui.
        if raw.startswith("/"):
            return RoutedIntent(Intent.CHAT, raw)

        normalized = _normalize(raw)

        for matcher in (
            self._match_percent,
            self._match_math,
            self._match_settings,
            self._match_open_url,
            self._match_open_folder,
            self._match_volume,
            self._match_screenshot,
            self._match_clipboard,
            self._match_processes,
            self._match_system_info,
            self._match_reminder,
            self._match_file_search,
            self._match_close_app,
            self._match_open_app,
        ):
            routed = matcher(normalized, raw)
            if routed is not None:
                return routed

        # Nada casou: é conversa. Este é o caminho da maioria das mensagens.
        return RoutedIntent(Intent.CHAT, raw)

    # ------------------------------------------------------------------

    def _match_percent(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _PERCENT_OF.match(normalized)
        if match is None:
            return None
        return RoutedIntent(
            Intent.TOOL, raw,
            {"tool": "calculator", "percent": match.group("percent"), "value": match.group("value")},
        )

    def _match_math(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _MATH.match(normalized)
        if match is None:
            return None
        expression = match.group("expr").strip()
        # Precisa ter um OPERADOR: sem isto, "2024" (um ano numa conversa)
        # viraria uma conta.
        if not expression or not any(op in expression for op in "+-*/^%"):
            return None
        if not any(ch.isdigit() for ch in expression):
            return None
        return RoutedIntent(Intent.TOOL, raw, {"tool": "calculator", "expression": expression})

    def _match_settings(self, normalized: str, raw: str) -> RoutedIntent | None:
        if _SETTINGS.fullmatch(normalized):
            return RoutedIntent(Intent.SETTINGS, raw, {"section": ""})
        return None

    def _match_open_url(self, normalized: str, raw: str) -> RoutedIntent | None:
        # Casa no texto ORIGINAL: normalizar destruiria a URL.
        match = _OPEN_URL.match(" ".join(raw.split()).lower())
        if match is None:
            return None
        # Extrai a URL do texto original preservando a caixa (paths de URL são
        # sensíveis a maiúscula/minúscula).
        original = re.search(r"https?://\S+", raw)
        url = original.group(0) if original else match.group("url")
        return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "open_url", "url": url})

    def _match_open_folder(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _OPEN_FOLDER.match(normalized)
        if match is None:
            return None
        return RoutedIntent(
            Intent.SYSTEM_ACTION, raw,
            {"action": "open_folder", "target": self._original_tail(raw, match.group("target"))},
        )

    def _match_volume(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _VOLUME_SET.match(normalized)
        if match is not None:
            value = int(match.group("value"))
            if value > 100:
                return None  # "volume 500" não é um pedido de volume válido
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "volume_set", "level": str(value)})
        if _VOLUME_UP.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "volume_up"})
        if _VOLUME_DOWN.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "volume_down"})
        if _MUTE.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "mute"})
        if _UNMUTE.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "unmute"})
        return None

    def _match_screenshot(self, normalized: str, raw: str) -> RoutedIntent | None:
        if _SCREENSHOT.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "screenshot"})
        return None

    def _match_clipboard(self, normalized: str, raw: str) -> RoutedIntent | None:
        if _CLIPBOARD_SUMMARIZE.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "clipboard_summarize"})
        if _CLIPBOARD_READ.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "clipboard_read"})
        return None

    def _match_processes(self, normalized: str, raw: str) -> RoutedIntent | None:
        if _PROCESSES.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "list_processes"})
        return None

    def _match_system_info(self, normalized: str, raw: str) -> RoutedIntent | None:
        if _SYSTEM_INFO.match(normalized):
            return RoutedIntent(Intent.SYSTEM_ACTION, raw, {"action": "system_info"})
        return None

    def _match_reminder(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _REMINDER.match(normalized)
        if match is None:
            return None
        # A v1.7 reconhece o pedido; quem AGENDA é o ReminderService da v1.9.
        # Reconhecer sem executar é melhor que mandar para a IA, que
        # responderia "claro, vou lembrar" sem lembrar de nada.
        return RoutedIntent(
            Intent.REMINDER, raw,
            {"what": self._original_tail(raw, match.group("what"))},
        )

    def _match_file_search(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _FILE_WHERE.match(normalized) or _FILE_SEARCH.match(normalized)
        if match is None:
            return None
        query = match.group("query")
        # Só é busca de ARQUIVO quando a frase diz isso. "acha um jeito de..."
        # e "procura saber se..." são conversa.
        if not any(hint in query for hint in ("arquivo", "pdf", "documento", "pasta", "file", "doc", "planilha", "trabalho")):
            return None
        return RoutedIntent(
            Intent.FILE_SEARCH, raw, {"query": self._original_tail(raw, query)}, confidence=0.8
        )

    def _match_close_app(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _CLOSE_APP.match(normalized)
        if match is None:
            return None
        target = match.group("target")
        # "mata todos os processos python" é um pedido de escopo amplo e
        # perigoso — não vira um comando de fechar aplicativo, vai para a IA,
        # que pode explicar em vez de executar.
        if any(word in target for word in ("todos", "todas", "all", "*")):
            return None
        return RoutedIntent(
            Intent.SYSTEM_ACTION, raw,
            {"action": "close_app", "target": self._original_tail(raw, target)},
        )

    def _match_open_app(self, normalized: str, raw: str) -> RoutedIntent | None:
        match = _OPEN_APP.match(normalized)
        if match is None:
            return None
        target = match.group("target")
        # Alvo longo demais quase nunca é nome de aplicativo — "abrir uma
        # discussão sobre o projeto" não é um pedido para abrir programa.
        if len(target.split()) > 4:
            return None
        return RoutedIntent(
            Intent.OPEN_APP, raw, {"target": self._original_tail(raw, target)}
        )

    @staticmethod
    def _original_tail(raw: str, normalized_tail: str) -> str:
        """Recupera o trecho ORIGINAL correspondente ao final normalizado.

        Necessário porque o casamento roda sobre o texto sem acento e em
        minúsculas, mas o parâmetro precisa da forma real: um nome de
        aplicativo, de pasta ou de arquivo é usado como está, e devolver
        "area de trabalho" onde o disco tem "Área de Trabalho" faria a busca
        falhar."""
        words = normalized_tail.split()
        if not words:
            return normalized_tail
        original_words = raw.split()
        tail = original_words[-len(words):] if len(original_words) >= len(words) else original_words
        return " ".join(tail).strip(" ?!.")
