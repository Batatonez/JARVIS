"""Tipos da camada de precisão do JARVIS (Accuracy Layer).

--------------------------------------------------------------------------
O problema que esta camada existe para resolver
--------------------------------------------------------------------------
Perguntado sobre "el ninho", o JARVIS respondeu, com confiança, que era um
chocolate da Nestlé. O modelo não reconheceu o termo e, em vez de dizer
isso, preencheu a lacuna com a coisa mais próxima que conhecia.

Esse é o modo de falha central de um assistente: **uma resposta errada dita
com confiança é pior que "preciso verificar isso"**. E não se corrige com
uma frase no system prompt pedindo humildade — o modelo continua sendo um
completador de texto que sempre tem algo a completar.

A correção é arquitetural: antes de responder, decidir DE FORMA
DETERMINÍSTICA se aquela pergunta pode ser respondida direto, precisa de
esclarecimento, precisa de verificação, ou precisa de evidência externa.

--------------------------------------------------------------------------
Por que tudo aqui é serializável
--------------------------------------------------------------------------
Estes tipos descrevem o que a interface mostra: o que o JARVIS está fazendo
agora e em que se baseou. Hoje isso vai para QML por sinal do Qt; amanhã
pode ir para uma interface web por SSE. `to_dict()` em tudo mantém as duas
com a mesma semântica, sem uma segunda definição divergindo.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


# ----------------------------------------------------------------------
# Decisão
# ----------------------------------------------------------------------


class AccuracyAction(str, Enum):
    """O que fazer com esta mensagem antes de responder."""

    # Conhecimento estável e pergunta clara: responder direto. É o caminho
    # da maioria das mensagens, e precisa continuar barato.
    DIRECT = "direct"
    # A pergunta tem mais de uma leitura razoável e escolher uma seria
    # adivinhar. Perguntar custa um turno; errar custa a confiança.
    CLARIFY = "clarify"
    # Dá para responder, mas a afirmação merece conferência antes de sair.
    VERIFY = "verify"
    # A resposta depende de informação que o modelo não tem como saber
    # sozinho (atual, específica, ou um termo que ele não reconhece).
    RESEARCH = "research"
    # Precisaria de evidência externa e ela não está disponível. Responder
    # assim mesmo seria exatamente o bug do "el ninho".
    REFUSE_UNVERIFIED = "refuse_unverified"


class UncertaintyReason(str, Enum):
    """Por que a resposta não é imediata. Enumerado (e não texto livre) para
    a decisão ser testável e para o log técnico ser agregável."""

    UNKNOWN_TERM = "unknown_term"
    POSSIBLE_TYPO = "possible_typo"
    AMBIGUOUS_TERM = "ambiguous_term"
    TEMPORALLY_UNSTABLE = "temporally_unstable"
    CONFLICTING_INFORMATION = "conflicting_information"
    MISSING_CONTEXT = "missing_context"
    LOW_EVIDENCE = "low_evidence"
    TOOL_UNAVAILABLE = "tool_unavailable"
    HIGH_STAKES = "high_stakes"
    USER_CHALLENGED = "user_challenged"
    OTHER = "other"


class FreshnessRequirement(str, Enum):
    """Quão rápido a resposta certa envelhece.

    É o que separa "explica fotossíntese" (a resposta de 1980 continua boa)
    de "qual a versão atual do Python" (a resposta do mês passado já pode
    estar errada). O modelo não tem noção disso sozinho — para ele as duas
    são texto igualmente familiar."""

    STATIC = "static"            # matemática, definição científica, história
    SEMI_STABLE = "semi_stable"  # specs de produto, documentação, compatibilidade
    VOLATILE = "volatile"        # preço, notícia, cargo, versão, placar, clima


@dataclass(frozen=True)
class AccuracyDecision:
    """O veredito do preflight. Nada aqui é texto para o usuário — é a
    instrução interna de como esta mensagem deve ser tratada."""

    action: AccuracyAction
    reasons: tuple[UncertaintyReason, ...] = ()
    normalized_query: str = ""
    # Leituras plausíveis quando o termo é ambíguo ou parece erro de
    # digitação. Nunca aplicadas em silêncio — ver `preflight.py`.
    possible_interpretations: tuple[str, ...] = ()
    freshness: FreshnessRequirement = FreshnessRequirement.STATIC
    requires_fresh_information: bool = False
    requires_external_evidence: bool = False
    safe_to_answer_from_model_knowledge: bool = True
    # Pista, não veredito: um modelo pode estar 96% confiante e errado. Só
    # entra na decisão junto com os sinais determinísticos.
    confidence_hint: float = 1.0
    metadata: dict = field(default_factory=dict)

    @property
    def is_fast_path(self) -> bool:
        return self.action is AccuracyAction.DIRECT and not self.reasons

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "reasons": [reason.value for reason in self.reasons],
            "freshness": self.freshness.value,
            "requiresFreshInformation": self.requires_fresh_information,
            "requiresExternalEvidence": self.requires_external_evidence,
            "possibleInterpretations": list(self.possible_interpretations),
        }


# ----------------------------------------------------------------------
# Evidência e fontes
# ----------------------------------------------------------------------


class EvidenceType(str, Enum):
    WEB_SOURCE = "web_source"
    LOCAL_FILE = "local_file"
    TOOL_RESULT = "tool_result"
    SYSTEM_RESULT = "system_result"
    USER_PROVIDED = "user_provided"
    # Deliberadamente separado dos demais: o que o modelo "sabe" NÃO é uma
    # fonte verificável. Aparece aqui para poder ser distinguido do resto —
    # nunca para ser contado como comprovação.
    MODEL_KNOWLEDGE = "model_knowledge"


# Tipos que constituem evidência EXTERNA de verdade. `MODEL_KNOWLEDGE` fica
# de fora por construção: é o núcleo da regra anti-alucinação.
VERIFIABLE_EVIDENCE_TYPES = frozenset(
    {
        EvidenceType.WEB_SOURCE,
        EvidenceType.LOCAL_FILE,
        EvidenceType.TOOL_RESULT,
        EvidenceType.SYSTEM_RESULT,
        EvidenceType.USER_PROVIDED,
    }
)


@dataclass(frozen=True)
class SourceReference:
    """Uma fonte que o usuário pode ver e abrir.

    Só existe se veio de uma execução real de ferramenta — ver
    `SourceRegistry.register`, que é o único caminho para criar uma."""

    source_id: str
    title: str
    domain: str = ""
    url: str = ""
    source_type: str = EvidenceType.WEB_SOURCE.value
    accessed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "sourceId": self.source_id,
            "title": self.title,
            "domain": self.domain,
            "url": self.url,
            "sourceType": self.source_type,
            "accessedAt": self.accessed_at.isoformat(),
        }


@dataclass(frozen=True)
class EvidenceItem:
    """Um pedaço de informação com procedência conhecida."""

    evidence_id: str
    evidence_type: EvidenceType
    title: str = ""
    snippet: str = ""
    source_id: str = ""
    relevance: float = 0.0
    # Autoridade da fonte (ver `source_quality.py`). Não é verdade absoluta:
    # para "o que os usuários estão reclamando", um fórum é MAIS relevante
    # que a documentação oficial.
    authority: float = 0.0
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_externally_verifiable(self) -> bool:
        return self.evidence_type in VERIFIABLE_EVIDENCE_TYPES

    def to_dict(self) -> dict:
        return {
            "evidenceId": self.evidence_id,
            "evidenceType": self.evidence_type.value,
            "title": self.title,
            "snippet": self.snippet,
            "sourceId": self.source_id,
            "relevance": self.relevance,
        }


# ----------------------------------------------------------------------
# Afirmações e verificação
# ----------------------------------------------------------------------


class ClaimStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"
    INFERENCE = "inference"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class Claim:
    """Uma afirmação factual extraída do rascunho, para conferência.
    Estrutura interna — normalmente não aparece para o usuário."""

    claim_id: str
    text: str
    factual: bool = True
    externally_verifiable: bool = True
    temporal: bool = False
    evidence_ids: tuple[str, ...] = ()
    status: ClaimStatus = ClaimStatus.NOT_APPLICABLE


class VerificationStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    NEEDS_REVISION = "needs_revision"
    CONFLICT = "conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus = VerificationStatus.NOT_RUN
    issues: tuple[dict, ...] = ()
    revised: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "issues": list(self.issues),
            "revised": self.revised,
        }


# ----------------------------------------------------------------------
# Activity Trace
# ----------------------------------------------------------------------


class ActivityType(str, Enum):
    """O que o sistema está fazendo. ATENÇÃO: isto descreve EXECUÇÃO, não
    pensamento. "Pesquisando" significa que uma busca rodou, não que o
    modelo considerou pesquisar. Ver o cabeçalho de `activity.py`."""

    THINKING = "thinking"
    INTERPRETING = "interpreting"
    CLARIFYING = "clarifying"
    SEARCHING_WEB = "searching_web"
    READING_SOURCE = "reading_source"
    SEARCHING_FILES = "searching_files"
    RUNNING_TOOL = "running_tool"
    COMPARING = "comparing"
    VERIFYING = "verifying"
    DRAFTING = "drafting"
    RESPONDING = "responding"
    COMPLETED = "completed"
    ERROR = "error"


class ActivityStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# Chave de tradução por tipo. Centralizadas aqui (e não espalhadas em QML)
# para entrarem no i18n que o projeto já tem — ver `services/i18n.py`.
ACTIVITY_LABEL_KEYS: dict[ActivityType, str] = {
    ActivityType.THINKING: "activity.thinking",
    ActivityType.INTERPRETING: "activity.interpreting",
    ActivityType.CLARIFYING: "activity.clarifying",
    ActivityType.SEARCHING_WEB: "activity.searching_web",
    ActivityType.READING_SOURCE: "activity.reading_source",
    ActivityType.SEARCHING_FILES: "activity.searching_files",
    ActivityType.RUNNING_TOOL: "activity.running_tool",
    ActivityType.COMPARING: "activity.comparing",
    ActivityType.VERIFYING: "activity.verifying",
    ActivityType.DRAFTING: "activity.drafting",
    ActivityType.RESPONDING: "activity.responding",
    ActivityType.COMPLETED: "activity.completed",
    ActivityType.ERROR: "activity.error",
}


@dataclass
class ActivityEvent:
    """Um passo do que o sistema fez.

    `metadata` guarda só números e rótulos (quantas fontes, qual provider) —
    **nunca** texto de raciocínio, prompt interno ou saída de classificador.
    A regra que separa Activity Trace de chain-of-thought vive aqui."""

    activity_type: ActivityType
    status: ActivityStatus = ActivityStatus.PENDING
    event_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    metadata: dict = field(default_factory=dict)
    source_ids: tuple[str, ...] = ()

    @property
    def label_key(self) -> str:
        return ACTIVITY_LABEL_KEYS.get(self.activity_type, "activity.thinking")

    def to_dict(self) -> dict:
        return {
            "type": "activity",
            "eventId": self.event_id,
            "activityType": self.activity_type.value,
            "status": self.status.value,
            "labelKey": self.label_key,
            "startedAt": self.started_at.isoformat(),
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": dict(self.metadata),
            "sourceIds": list(self.source_ids),
        }
