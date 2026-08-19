"""Estado de UMA geração: fontes, evidências e atividade (Accuracy Layer).

--------------------------------------------------------------------------
Por que tudo aqui é criado por request
--------------------------------------------------------------------------
Este projeto já teve um bug de contaminação entre turnos: uma recusa vazava
para a mensagem seguinte. A correção da v1.6 foi tornar o estado de geração
explícito e local (`RequestContext` em `services/providers/router.py`).

Aqui vale a mesma regra, com mais motivo: se as FONTES vazassem entre
requests, o JARVIS mostraria "4 fontes" numa resposta em que não pesquisou
nada — que é uma forma de mentir sobre a própria procedência.

Por isso nada neste módulo é global, singleton ou atributo de serviço de
longa vida. O contexto nasce na request e morre com ela.

--------------------------------------------------------------------------
A regra que impede fonte fabricada
--------------------------------------------------------------------------
`SourceRegistry.register` é o ÚNICO caminho para uma fonte existir, e ele
exige a URL que uma ferramenta realmente retornou. Um modelo pode escrever
`"sources": ["https://noaa.gov/..."]` no meio da resposta — isso é texto,
não passa por aqui, e portanto nunca vira uma fonte que a interface mostre.

O corolário prático: se `sources` está vazio, a interface não mostra botão
de fontes. Não existe caminho em que o JARVIS afirme ter pesquisado sem ter
pesquisado.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from uuid import uuid4

from services.accuracy.models import (
    AccuracyDecision,
    ActivityEvent,
    ActivityStatus,
    ActivityType,
    EvidenceItem,
    EvidenceType,
    SourceReference,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# Só estes esquemas viram fonte clicável. `javascript:` e `data:` executam;
# `file:` alcança o disco do usuário a partir de conteúdo remoto.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Teto de fontes mostradas. Não é limite de pesquisa — é limite de PAINEL:
# vinte resultados quase iguais não ajudam ninguém a conferir nada.
MAX_VISIBLE_SOURCES = 10

_TITLE_MAX = 200
_SNIPPET_MAX = 500

# Caracteres de controle e marcação que não têm por que existir num título
# vindo da web. Removidos antes de o texto chegar ao QML.
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f\x7f<>]")

# Chaves permitidas na metadata de um evento de atividade. Cada uma existe
# porque a interface precisa dela para descrever o que aconteceu — nunca
# porque "pode ser útil".
#
#   count / sources / results   quantidades ("Verificando 4 fontes")
#   query_length                tamanho da busca, sem o texto da busca
#   provider / model            rota usada, já com nome legível
#   status_code                 falha de rede numa fonte
#   error                       código curto, nunca traceback
#   revised / attempt           estado do verifier
#   fast_path / reason          por que a decisão foi tomada
ACTIVITY_METADATA_KEYS = frozenset(
    {
        "count", "sources", "results", "query_length", "provider", "model",
        "status_code", "error", "revised", "attempt", "fast_path", "reason",
        "elapsed_ms", "available",
    }
)


def sanitize_display_text(value: str, *, limit: int = _TITLE_MAX) -> str:
    """Texto vindo de fonte externa, seguro para exibir.

    Conteúdo de página é dado hostil por natureza: pode conter caractere de
    controle, marcação, ou uma linha inteira tentando parecer parte da
    interface. O QML recebe texto puro, com tamanho limitado."""
    cleaned = _UNSAFE_TEXT.sub(" ", value or "")
    return " ".join(cleaned.split())[:limit]


def normalize_url(url: str) -> str | None:
    """URL segura para virar link, ou `None`.

    Recusa esquema não suportado, URL sem host e credencial embutida
    (`https://user:senha@host`), que é um vetor clássico de phishing em
    lista de fontes."""
    candidate = (url or "").strip()
    if not candidate:
        return None
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None
    if not parsed.netloc:
        return None
    if "@" in parsed.netloc:
        return None
    return candidate


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def _canonical_key(url: str) -> str:
    """Chave de deduplicação: mesmo host + mesmo caminho, ignorando query e
    fragmento. Sem isto, três links do mesmo artigo com parâmetros de
    campanha diferentes apareceriam como três fontes."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url.lower()
    path = parsed.path.rstrip("/").lower()
    return f"{domain_of(url)}{path}"


class SourceRegistry:
    """Fontes REAIS desta request.

    Nunca é preenchido a partir de texto do modelo — só por quem executou
    uma ferramenta de verdade."""

    def __init__(self) -> None:
        self._sources: dict[str, SourceReference] = {}
        self._by_canonical: dict[str, str] = {}

    def register(
        self,
        *,
        title: str,
        url: str = "",
        source_type: EvidenceType = EvidenceType.WEB_SOURCE,
        accessed_at: datetime | None = None,
    ) -> SourceReference | None:
        """Registra uma fonte que uma ferramenta realmente retornou.

        Devolve `None` quando a URL é inaceitável — e o chamador NÃO deve
        tratar isso como erro: significa apenas que aquele resultado não
        entra na lista de fontes.

        Fonte web sem URL é recusada de propósito: uma "fonte" que o usuário
        não pode abrir e conferir não é uma fonte, é uma afirmação."""
        if source_type is EvidenceType.MODEL_KNOWLEDGE:
            # O que o modelo "sabe" não é procedência. Aceitar isto aqui
            # transformaria memória em citação.
            logger.debug("Recusando registrar MODEL_KNOWLEDGE como fonte.")
            return None

        safe_url = ""
        if source_type is EvidenceType.WEB_SOURCE:
            normalized = normalize_url(url)
            if normalized is None:
                logger.info("Fonte web recusada: URL ausente ou com esquema não suportado.")
                return None
            safe_url = normalized
            key = _canonical_key(safe_url)
            existing = self._by_canonical.get(key)
            if existing is not None:
                return self._sources[existing]
        else:
            key = f"{source_type.value}:{sanitize_display_text(title)}"
            existing = self._by_canonical.get(key)
            if existing is not None:
                return self._sources[existing]

        source = SourceReference(
            source_id=f"src_{len(self._sources) + 1}",
            title=sanitize_display_text(title) or domain_of(safe_url) or "Fonte",
            domain=domain_of(safe_url),
            url=safe_url,
            source_type=source_type.value,
            accessed_at=accessed_at or datetime.now(timezone.utc),
        )
        self._sources[source.source_id] = source
        self._by_canonical[key] = source.source_id
        return source

    def get(self, source_id: str) -> SourceReference | None:
        return self._sources.get(source_id)

    def all(self) -> list[SourceReference]:
        return list(self._sources.values())

    def visible(self) -> list[SourceReference]:
        return self.all()[:MAX_VISIBLE_SOURCES]

    @property
    def count(self) -> int:
        return len(self._sources)

    def to_views(self) -> list[dict]:
        return [source.to_dict() for source in self.visible()]


class ActivityTrace:
    """O que o sistema fez nesta request, em ordem.

    **Isto não é chain-of-thought.** A distinção não é de grau, é de
    natureza: um evento aqui significa "esta operação foi EXECUTADA", e é
    criado pelo código que a executa. Não existe caminho pelo qual texto de
    raciocínio do modelo entre — nenhum método aceita conteúdo livre, só
    tipo, status e metadata numérica.

    Consequência prática: se `SEARCHING_WEB` aparece, uma busca rodou de
    verdade. A interface pode confiar no que mostra."""

    def __init__(self) -> None:
        self._events: list[ActivityEvent] = []

    def start(self, activity_type: ActivityType, **metadata) -> ActivityEvent:
        event = ActivityEvent(
            activity_type=activity_type,
            status=ActivityStatus.RUNNING,
            metadata=self._safe_metadata(metadata),
        )
        self._events.append(event)
        return event

    def complete(self, event: ActivityEvent, *, source_ids=(), **metadata) -> ActivityEvent:
        event.status = ActivityStatus.DONE
        event.completed_at = datetime.now(timezone.utc)
        event.metadata.update(self._safe_metadata(metadata))
        if source_ids:
            event.source_ids = tuple(source_ids)
        return event

    def fail(self, event: ActivityEvent, *, reason: str = "") -> ActivityEvent:
        event.status = ActivityStatus.FAILED
        event.completed_at = datetime.now(timezone.utc)
        if reason:
            # Código de erro curto, nunca traceback nem mensagem de provider.
            event.metadata["error"] = sanitize_display_text(reason, limit=80)
        return event

    @staticmethod
    def _safe_metadata(metadata: dict) -> dict:
        """Só chaves da ALLOWLIST, e só valores simples.

        Truncar texto desconhecido não bastaria: sessenta caracteres de
        raciocínio interno ainda são raciocínio interno vazando para a
        interface. Uma allowlist fechada torna isso impossível por
        construção — uma chave nova precisa ser adicionada aqui de
        propósito, o que é exatamente o ponto de revisão que se quer ter.

        É o mesmo padrão de `services/security_event_repository.py`, pelo
        mesmo motivo: um canal de observabilidade não pode virar um canal de
        vazamento."""
        safe: dict = {}
        for key, value in (metadata or {}).items():
            if key not in ACTIVITY_METADATA_KEYS:
                logger.warning("Metadata '%s' fora da allowlist do Activity Trace — descartada.", key)
                continue
            if isinstance(value, bool) or isinstance(value, (int, float)):
                safe[key] = value
            elif value is None:
                continue
            else:
                safe[key] = sanitize_display_text(str(value), limit=60)
        return safe

    def events(self) -> list[ActivityEvent]:
        return list(self._events)

    def to_views(self) -> list[dict]:
        return [event.to_dict() for event in self._events]

    @property
    def is_empty(self) -> bool:
        return not self._events

    def has(self, activity_type: ActivityType) -> bool:
        return any(event.activity_type is activity_type for event in self._events)


@dataclass
class AccuracyRequestContext:
    """Tudo que pertence a UMA geração.

    Criado no início da request e descartado no fim. Um teste percorre os
    atributos de instância dos serviços para garantir que nada disto virou
    estado de longa vida."""

    request_id: str = field(default_factory=lambda: str(uuid4()))
    decision: AccuracyDecision | None = None
    sources: SourceRegistry = field(default_factory=SourceRegistry)
    activity: ActivityTrace = field(default_factory=ActivityTrace)
    evidence: list[EvidenceItem] = field(default_factory=list)
    verification: VerificationResult = field(default_factory=VerificationResult)
    # Métricas por etapa, para otimizar depois sem adivinhar onde está o
    # custo (item 65 do escopo).
    timings_ms: dict = field(default_factory=dict)

    def add_evidence(
        self,
        *,
        evidence_type: EvidenceType,
        title: str = "",
        snippet: str = "",
        source_id: str = "",
        relevance: float = 0.0,
        authority: float = 0.0,
    ) -> EvidenceItem:
        item = EvidenceItem(
            evidence_id=f"ev_{len(self.evidence) + 1}",
            evidence_type=evidence_type,
            title=sanitize_display_text(title),
            snippet=sanitize_display_text(snippet, limit=_SNIPPET_MAX),
            source_id=source_id,
            relevance=relevance,
            authority=authority,
        )
        self.evidence.append(item)
        return item

    @property
    def external_evidence(self) -> list[EvidenceItem]:
        """Só o que é verificável fora do modelo. É esta lista — e não a
        completa — que decide se uma afirmação tem respaldo."""
        return [item for item in self.evidence if item.is_externally_verifiable]

    @property
    def has_external_evidence(self) -> bool:
        return bool(self.external_evidence)

    def summary(self) -> dict:
        """Resumo seguro para o HUD e para log técnico. Sem conteúdo de
        resposta, sem prompt, sem raciocínio."""
        return {
            "requestId": self.request_id,
            "action": self.decision.action.value if self.decision else "direct",
            "sourceCount": self.sources.count,
            "evidenceCount": len(self.external_evidence),
            "verification": self.verification.status.value,
            "activity": self.activity.to_views(),
            "sources": self.sources.to_views(),
            "timingsMs": dict(self.timings_ms),
        }
