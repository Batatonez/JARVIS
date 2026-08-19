"""Análise da mensagem ANTES de gerar resposta (Accuracy Layer).

--------------------------------------------------------------------------
O que este módulo é, e o que ele não é
--------------------------------------------------------------------------
Ele **classifica**. Não escreve resposta, não chama modelo, não toca rede.
Roda em microssegundos, sobre a própria frase, e decide se aquela mensagem
pode ser respondida direto ou precisa de mais alguma coisa.

Ser determinístico é o ponto. Perguntar a um modelo "você tem certeza?"
herda exatamente o problema que estamos corrigindo: o modelo que inventou o
chocolate da Nestlé também teria dito que estava seguro.

--------------------------------------------------------------------------
A assimetria que orienta todas as decisões daqui
--------------------------------------------------------------------------
Os dois erros possíveis não custam o mesmo:

    verificar algo que era óbvio       -> custa um pouco de latência
    responder algo que era desconhecido -> custa a confiança do usuário

Então, na dúvida, verifica. Mas com cuidado para o "na dúvida" não engolir
tudo: "oi" e "2+2" precisam continuar instantâneos, ou a camada de precisão
vira um imposto sobre cada mensagem.

--------------------------------------------------------------------------
Como o caso "el ninho" é detectado — sem hardcode e sem blacklist
--------------------------------------------------------------------------
Nada aqui conhece "el ninho", "El Niño" ou "Nestlé". O que é detectado são
PROPRIEDADES da frase:

1. é uma pergunta de definição ("o que é X", "quem é X");
2. o X é um termo que não parece palavra comum do idioma;
3. o X tem forma de nome próprio/estrangeiro/possível erro de digitação.

Qualquer termo com essas propriedades — hoje "el ninho", amanhã outro — cai
em VERIFY/RESEARCH. É por isso que o teste de regressão não afirma nada
sobre El Niño: ele afirma que um termo desconhecido não vira definição
inventada.
"""

import re
import unicodedata
from datetime import datetime, timezone

from services.accuracy.models import (
    AccuracyAction,
    AccuracyDecision,
    FreshnessRequirement,
    UncertaintyReason,
)

# ----------------------------------------------------------------------
# Fast path — o que NUNCA deve pagar o custo da camada de precisão
# ----------------------------------------------------------------------

_GREETINGS = frozenset(
    {
        "oi", "ola", "olá", "eai", "e ai", "eaí", "opa", "bom dia", "boa tarde",
        "boa noite", "hey", "hi", "hello", "salve", "fala", "tudo bem",
        "obrigado", "obrigada", "valeu", "vlw", "thanks", "tchau", "ate mais",
        "até mais", "bye", "ok", "beleza", "certo", "entendi",
    }
)

# Pedidos criativos/de transformação: a saída é gerada, não factual. Pedir
# fonte para uma piada não faz sentido.
_CREATIVE_MARKERS = (
    "escreve", "escreva", "reescreve", "reescreva", "resuma este texto",
    "traduz", "traduza", "faz uma piada", "faça uma piada", "conta uma piada",
    "cria um", "crie um", "invente", "imagine", "poema", "história sobre",
    "historia sobre", "brainstorm", "sugestões de nome", "sugestoes de nome",
    "corrige a gramática", "corrige a gramatica", "formata", "formate",
    "write", "rewrite", "translate", "summarize this text", "make up a",
)

_MATH_ONLY = re.compile(r"^[\d\s().+\-*/^%=,]+$")

# ----------------------------------------------------------------------
# Gatilhos de informação atual (VOLATILE)
# ----------------------------------------------------------------------
# Cada um destes indica que a resposta certa muda com o tempo. O modelo não
# tem como saber que envelheceu — para ele, o texto de treino continua
# igualmente familiar.

_VOLATILE_MARKERS = (
    "atual", "atualmente", "hoje", "agora", "neste momento", "no momento",
    "mais recente", "recente", "ultima versao", "última versão", "ultimo",
    "último", "latest", "newest", "current", "currently", "today", "right now",
    "preco", "preço", "custa", "quanto custa", "cotacao", "cotação", "valor de",
    "presidente", "ceo", "primeiro-ministro", "prefeito", "governador",
    "lancamento", "lançamento", "lancou", "lançou", "saiu",
    "placar", "resultado do jogo", "campeao", "campeão", "classificacao",
    "noticia", "notícia", "noticias", "notícias", "aconteceu",
    "clima", "tempo em", "previsao", "previsão", "temperatura em",
    "disponivel", "disponível", "em estoque", "esgotado",
    "versao atual", "versão atual", "release", "changelog",
    "este ano", "esse ano", "este mes", "este mês", "nesta semana",
)

# Assuntos cuja resposta certa muda devagar, mas muda: especificação de
# produto, compatibilidade, documentação de API.
_SEMI_STABLE_MARKERS = (
    "especificacao", "especificação", "spec", "requisitos", "compativel",
    "compatível", "compatibilidade", "suporta", "documentacao", "documentação",
    "api", "biblioteca", "framework", "instalar", "configurar",
    "modelo", "gpu", "placa de video", "placa de vídeo", "processador",
)

# ----------------------------------------------------------------------
# Perguntas de definição — onde o bug do "el ninho" mora
# ----------------------------------------------------------------------

_DEFINITION_PATTERNS = (
    re.compile(r"\b(?:o\s+que|oq|q)\s+(?:e|eh|é|era|foi|seria|significa)\b"),
    re.compile(r"\bquem\s+(?:e|eh|é|era|foi)\b"),
    re.compile(r"\bwhat\s+(?:is|was|are|were)\b"),
    re.compile(r"\bwho\s+(?:is|was)\b"),
    re.compile(r"\bsignificado\s+de\b"),
    re.compile(r"\bdefini(?:cao|ção)\s+de\b"),
    re.compile(r"\bme\s+(?:fala|diga|explica|explique)\s+(?:sobre|o\s+que)\b"),
)

# Desafio explícito do usuário — item 73/74 do escopo. "tem certeza?" é um
# sinal forte: responder "sim" sem conferir é o pior comportamento possível.
_CHALLENGE_PATTERNS = (
    re.compile(r"\btem\s+certeza\b"),
    re.compile(r"\bvoce\s+tem\s+certeza\b"),
    re.compile(r"\bcerteza\s+disso\b"),
    re.compile(r"\bare\s+you\s+sure\b"),
    re.compile(r"\bisso\s+(?:esta|está|ta|tá)\s+errado\b"),
    re.compile(r"\b(?:nao|não)\s*,?\s*isso\s+(?:esta|está|ta|tá)\s+errado\b"),
    re.compile(r"\bvoce\s+errou\b"),
    re.compile(r"\bvocê\s+errou\b"),
    re.compile(r"\bmentira\b"),
    re.compile(r"\bconfere\s+isso\b"),
    re.compile(r"\bverifica\s+isso\b"),
)

# Ambiguidade declarada pelo próprio usuário: "X significa A ou B?"
_EXPLICIT_ALTERNATIVES = re.compile(r"\b(.{2,40}?)\s+ou\s+(.{2,40}?)\s*\?")

# ----------------------------------------------------------------------
# Léxico mínimo de português/inglês
# ----------------------------------------------------------------------
# NÃO é um dicionário. É o conjunto de palavras funcionais que aparecem em
# qualquer frase — usado só para descartá-las ao procurar o TERMO da
# pergunta. Uma palavra fora daqui não é "desconhecida"; é candidata a
# termo, e a decisão sobre ela vem das propriedades ortográficas abaixo.

_FUNCTION_WORDS = frozenset(
    """
    o a os as um uma uns umas de do da dos das em no na nos nas por para com
    sem sobre entre ate ate' e ou mas que quem qual quais quando onde como
    porque por que pq se nao sim eh e' era foi ser sao sou esta estao este
    esse aquele isso isto aquilo meu minha seu sua nosso muito mais menos
    tambem so apenas ja ainda depois antes agora hoje ontem amanha
    the of in on at to for with without about from and or but that which who
    what when where how why is are was were be been being do does did a an
    me te lhe nos vos lhes eu tu ele ela nos vos eles elas voce voces
    fala diga explica explique conta me sabe entao pois assim
    coisa coisas algo alguem tudo nada
    """.split()
)

# Formas que sugerem NOME PRÓPRIO ou termo estrangeiro em vez de palavra
# comum. São pistas ortográficas, não conhecimento sobre o mundo.
_FOREIGN_ARTICLE_PREFIXES = ("el ", "la ", "le ", "les ", "il ", "der ", "das ", "det ")
_UNCOMMON_LETTER_CLUSTERS = ("nh", "lh", "sch", "zh", "kh", "gn", "ll", "rr", "tz", "ck")

# Terminações derivacionais produtivas do português e do inglês. Uma palavra
# que termina assim quase sempre é substantivo comum do idioma —
# "fotossíntese", "eletricidade", "capitalismo", "photosynthesis".
#
# Isto NÃO é um dicionário nem uma lista de assuntos permitidos: é
# morfologia. É o sinal que separa uma palavra do idioma de um nome próprio
# ou de uma grafia fonética como "gimine", que não tem terminação
# reconhecível de substantivo comum.
#
# O critério é deliberadamente permissivo — errar aqui só faz o JARVIS
# CONFERIR algo que já sabia, que é o lado barato do erro.
_COMMON_NOUN_ENDINGS = (
    # português
    "cao", "coes", "dade", "dades", "ismo", "ismos", "ista", "istas",
    "encia", "encias", "ancia", "ancias", "ente", "entes", "ura", "uras",
    "agem", "agens", "mento", "mentos", "logia", "logias", "grafia", "grafias",
    "ese", "eses", "ose", "oses", "itis", "ite", "idade", "eiro", "eira",
    "ario", "aria", "orio", "oria", "avel", "ivel", "ancia", "tica", "tico",
    # inglês
    "tion", "sion", "ness", "ment", "ity", "ance", "ence", "ology", "ography",
    "ism", "ist", "esis", "osis", "graphy", "metry", "sophy",
)

# Palavras curtas demais para carregar morfologia. Abaixo disto, a ausência
# de terminação reconhecível não diz nada.
_MIN_MORPHOLOGY_LENGTH = 7


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalize(text: str) -> str:
    """Minúsculas, sem acento, sem espaço duplicado. Só para CASAR padrão —
    o texto original nunca é alterado."""
    return " ".join(_strip_accents(text or "").lower().split())


def _contains_any(haystack: str, needles) -> bool:
    """Casa marcadores respeitando FRONTEIRA DE PALAVRA.

    Substring cru produz falso positivo silencioso e difícil de notar:
    "capitalismo" contém "api", "presidente" contém "reside", "hoje" aparece
    dentro de outras palavras. Um único falso positivo desses manda uma
    pergunta estática para o caminho de pesquisa — e a pessoa só percebe que
    o JARVIS ficou lento sem motivo."""
    for needle in needles:
        # Marcador com espaço é uma expressão; casa como frase, mas ainda
        # com fronteira nas pontas.
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
        if re.search(pattern, haystack):
            return True
    return False


def _extract_definition_subject(normalized: str) -> str:
    """O termo sobre o qual se está perguntando, em "o que é X".

    Devolve string vazia quando a frase não é uma pergunta de definição ou
    quando o sujeito é composto só de palavras funcionais."""
    for pattern in _DEFINITION_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        tail = normalized[match.end():].strip(" ?!.,:;")
        # Remove artigo/preposição inicial que sobrou do padrão.
        words = [word for word in tail.split() if word]
        while words and words[0] in ("o", "a", "os", "as", "um", "uma", "de", "do", "da"):
            words.pop(0)
        if not words:
            return ""
        # Sujeitos longos são frases, não termos — "o que é aquilo que você
        # falou ontem sobre o projeto" não é uma pergunta de definição de
        # termo.
        if len(words) > 5:
            return ""
        return " ".join(words)
    return ""


def _looks_unfamiliar(term: str) -> bool:
    """O termo tem forma de nome próprio, estrangeirismo ou erro de digitação?

    Puramente ortográfico — este código não sabe o que os termos significam,
    e é exatamente isso que o torna geral. As pistas:

    - começa com artigo estrangeiro ("el", "la", "der");
    - contém agrupamento de letras incomum em palavra corrente;
    - não é composto apenas de palavras funcionais conhecidas.
    """
    if not term:
        return False
    normalized = _normalize(term)
    words = normalized.split()

    # Só palavras funcionais: não é um termo, é uma frase vaga.
    content_words = [word for word in words if word not in _FUNCTION_WORDS]
    if not content_words:
        return False

    if normalized.startswith(_FOREIGN_ARTICLE_PREFIXES):
        return True
    # Agrupamentos de letras raros em palavra corrente. Aqui a busca é por
    # SUBSTRING de propósito — um cluster é uma sequência dentro da palavra,
    # não uma palavra inteira.
    if any(cluster in normalized for cluster in _UNCOMMON_LETTER_CLUSTERS):
        return True

    # Identificador de produto/modelo: "rtx 4070", "glm 5.2", "gpt-oss 20b".
    # Os dígitos podem estar no mesmo token ("4070ti") ou num token separado
    # ("glm" + "5.2") — as duas formas são o mesmo padrão, e exigir que
    # estivessem juntos deixava metade dos casos passar.
    has_alpha_token = any(any(ch.isalpha() for ch in word) for word in content_words)
    has_digit = any(any(ch.isdigit() for ch in word) for word in content_words)
    if has_alpha_token and has_digit:
        return True
    return False


def _has_common_morphology(term: str) -> bool:
    """A palavra tem forma de substantivo comum do idioma?

    É o sinal que distingue "fotossíntese" (termina em `-ese`, morfologia
    produtiva do português) de "gimine" (nenhuma terminação reconhecível —
    grafia fonética de um nome próprio).

    Palavra curta não é avaliada: "água" e "casa" não têm sufixo derivacional
    e nem por isso são desconhecidas. Para elas vale a regra geral — termo
    curto e sem sinal nenhum cai em VERIFY, que é barato."""
    normalized = _normalize(term)
    content = [word for word in normalized.split() if word not in _FUNCTION_WORDS]
    if not content:
        return False
    return any(
        len(word) >= _MIN_MORPHOLOGY_LENGTH and word.endswith(_COMMON_NOUN_ENDINGS)
        for word in content
    )


def _is_capitalized_in_original(term: str, original: str) -> bool:
    """O termo aparece com inicial maiúscula no MEIO da frase original?

    Sinal forte de nome próprio — e nome próprio é justamente o que o modelo
    não deve tentar definir de memória. Precisa do texto original porque a
    normalização apaga a caixa."""
    words = [word for word in term.split() if word]
    if not words:
        return False
    original_words = original.split()
    for index, word in enumerate(original_words):
        cleaned = word.strip(" ?!.,:;\"'")
        if not cleaned:
            continue
        if _normalize(cleaned) == words[0]:
            # Primeira palavra da frase não conta: maiúscula ali é
            # ortografia normal, não indicação de nome próprio.
            return index > 0 and cleaned[0].isupper()
    return False


def _has_uncommon_shape(term: str) -> bool:
    """Termo curto o bastante para ser uma entidade nomeada.

    Sinal mais fraco que `_looks_unfamiliar`: serve para VERIFY (conferir) e
    não para RESEARCH (buscar) — a diferença entre "não reconheço isto" e
    "isto pode ser um nome"."""
    normalized = _normalize(term)
    content = [word for word in normalized.split() if word not in _FUNCTION_WORDS]
    return bool(content) and 1 <= len(content) <= 3


def classify_freshness(normalized: str) -> FreshnessRequirement:
    """Quão rápido a resposta certa para esta pergunta envelhece."""
    if _contains_any(normalized, _VOLATILE_MARKERS):
        return FreshnessRequirement.VOLATILE
    if _contains_any(normalized, _SEMI_STABLE_MARKERS):
        return FreshnessRequirement.SEMI_STABLE
    return FreshnessRequirement.STATIC


def is_fast_path_message(text: str) -> bool:
    """Mensagens que não merecem nenhuma análise: saudação, agradecimento,
    conta pura, pedido criativo.

    Sem isto, "oi" custaria a mesma latência de uma pesquisa — e a camada de
    precisão passaria a ser sentida como lentidão em vez de confiabilidade."""
    normalized = _normalize(text).strip(" ?!.,")
    if not normalized:
        return True
    if normalized in _GREETINGS:
        return True
    if _MATH_ONLY.match(normalized.replace(" ", "")) and any(ch.isdigit() for ch in normalized):
        return True
    if _contains_any(normalized, _CREATIVE_MARKERS):
        return True
    # Mensagem muito curta sem forma de pergunta factual.
    if len(normalized.split()) <= 2 and "?" not in text:
        return True
    return False


def analyze(text: str, *, now: datetime | None = None) -> AccuracyDecision:
    """Decide como esta mensagem deve ser tratada.

    `now` é injetável para os testes de freshness não dependerem do relógio
    da máquina — nunca para produção passar um tempo de fora."""
    now = now or datetime.now(timezone.utc)
    raw = (text or "").strip()
    normalized = _normalize(raw)

    if not raw:
        return AccuracyDecision(action=AccuracyAction.DIRECT, normalized_query="")

    # --- Fast path -----------------------------------------------------
    if is_fast_path_message(raw):
        return AccuracyDecision(
            action=AccuracyAction.DIRECT,
            normalized_query=normalized,
            freshness=FreshnessRequirement.STATIC,
            metadata={"fast_path": True},
        )

    reasons: list[UncertaintyReason] = []
    interpretations: list[str] = []
    freshness = classify_freshness(normalized)

    # --- Usuário desafiou a resposta anterior --------------------------
    # Verificado ANTES do resto: "tem certeza?" é sobre a resposta passada, e
    # a frase em si não tem termo nem marcador temporal.
    challenged = any(pattern.search(normalized) for pattern in _CHALLENGE_PATTERNS)
    if challenged:
        reasons.append(UncertaintyReason.USER_CHALLENGED)

    # --- Alternativas explícitas ("A ou B?") ---------------------------
    alternatives = _EXPLICIT_ALTERNATIVES.search(normalized)
    if alternatives is not None:
        left = alternatives.group(1).strip()
        right = alternatives.group(2).strip()
        if left and right and left != right:
            reasons.append(UncertaintyReason.AMBIGUOUS_TERM)
            interpretations.extend([left, right])

    # --- Termo de uma pergunta de definição ----------------------------
    subject = _extract_definition_subject(normalized)
    unfamiliar = False
    unverified_term = False
    if subject:
        if _looks_unfamiliar(subject) or _is_capitalized_in_original(subject, raw):
            # Forma de nome próprio, estrangeirismo ou identificador de
            # produto. É o caso do "el ninho": nunca responder de memória.
            unfamiliar = True
            reasons.append(UncertaintyReason.UNKNOWN_TERM)
            reasons.append(UncertaintyReason.POSSIBLE_TYPO)
            interpretations.append(subject)
        elif not _has_common_morphology(subject) and _has_uncommon_shape(subject):
            # Não parece nome próprio, mas também não tem morfologia de
            # substantivo comum — "gimine". Pode ser palavra que este código
            # não reconhece OU grafia fonética de um nome. Conferir é o lado
            # barato: se for palavra comum, o verifier passa direto.
            unverified_term = True
            reasons.append(UncertaintyReason.UNKNOWN_TERM)
            interpretations.append(subject)

    # --- Informação que envelhece --------------------------------------
    if freshness is FreshnessRequirement.VOLATILE:
        if UncertaintyReason.TEMPORALLY_UNSTABLE not in reasons:
            reasons.append(UncertaintyReason.TEMPORALLY_UNSTABLE)

    # --- Decisão -------------------------------------------------------
    action = AccuracyAction.DIRECT
    requires_external = False
    requires_fresh = freshness is FreshnessRequirement.VOLATILE

    if unfamiliar:
        # Termo que não reconhecemos: é EXATAMENTE o caso do "el ninho".
        # Nunca DIRECT — o modelo preencheria a lacuna.
        action = AccuracyAction.RESEARCH
        requires_external = True
    elif UncertaintyReason.AMBIGUOUS_TERM in reasons:
        # Duas leituras plausíveis declaradas pelo próprio usuário: escolher
        # uma seria adivinhar.
        action = AccuracyAction.CLARIFY
    elif requires_fresh:
        action = AccuracyAction.RESEARCH
        requires_external = True
    elif unverified_term:
        action = AccuracyAction.VERIFY
    elif challenged:
        action = AccuracyAction.VERIFY
    elif freshness is FreshnessRequirement.SEMI_STABLE:
        action = AccuracyAction.VERIFY

    safe_from_memory = action in (AccuracyAction.DIRECT, AccuracyAction.VERIFY)

    return AccuracyDecision(
        action=action,
        reasons=tuple(dict.fromkeys(reasons)),
        normalized_query=normalized,
        possible_interpretations=tuple(dict.fromkeys(interpretations)),
        freshness=freshness,
        requires_fresh_information=requires_fresh,
        requires_external_evidence=requires_external,
        safe_to_answer_from_model_knowledge=safe_from_memory,
        metadata={"subject": subject, "challenged": challenged, "now": now.isoformat()},
    )
