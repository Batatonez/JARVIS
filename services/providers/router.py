"""`ProviderRouter` — a única autoridade sobre "qual provider, qual modelo,
qual ordem, qual fallback, qual retry, qual classificação de erro, qual
política free_only" no JARVIS (ver `docs/providers.md`). Ruflo não participa
desta decisão: nada aqui chama `agent_spawn`/`agent_execute` (ver
`services/providers/ruflo_coordinator.py` e `docs/ruflo-integration.md` para
o bug de model routing que motivou manter essas duas responsabilidades
separadas — e a v1.4.0 não muda isso: ainda é só ProviderRouter, nunca
Ruflo, decidindo provider/modelo).

**v1.4.0 — cadeia de fallback centralizada (item 2).** Até a v1.3.2 só
existia UM provider real (OpenRouter) e um retry estreito, específico para
resposta vazia, vivendo em `ProviderRouterAIService`. Com 6 providers na
cadeia (OpenRouter → NVIDIA → Gemini → Groq → Cerebras → Mistral), essa
lógica precisava de um lar único e testável — e esse lar é aqui, não em
`AIService`/`ChatService`/Bridge/QML/Ruflo (item 2 é explícito sobre isso).

Defesa em duas camadas para `free_only=True` (nunca confiar só no que foi
pedido):
1. **Antes da chamada** (`_candidates()`): um candidato só entra na lista se
   o modelo pertence a `provider.free_models()` — nunca um modelo pago é
   sequer solicitado.
2. **Depois da chamada** (`_verify_free_or_raise`): a resposta é conferida
   contra o que o provider *relatou* ter servido (`served_model`/`cost`).
   Providers que reportam custo real (só a OpenRouter, nesta versão) são
   verificados por `cost.is_free`; os demais (NVIDIA/Gemini/Groq/
   Cerebras/Mistral, que nunca expõem custo por chamada) são verificados
   por **allowlist**: `served_model` precisa bater com a lista curada — ver
   `docs/providers.md`, seção "free-only sem metadata de custo confiável"
   (item 15, limitação documentada).

**Recuperável vs. não-recuperável (itens 16-19)**: o loop de fallback só
intercepta `RecoverableProviderError` (+ os dois casos especiais,
`NoFreeModelAvailableError`/`EmptyProviderResponseError`, que só o próprio
router consegue detectar). Um `NonRecoverableProviderError` — credencial
quebrada, request inválido, resposta malformada — propaga NA HORA, sem
tentar o próximo candidato: mascarar isso com outro provider esconderia
exatamente o tipo de bug que precisa aparecer (item 18). Qualquer exceção
que não seja `ProviderError` (um `TypeError` nosso, por exemplo) também
atravessa sem ser interceptada — não existe `except Exception` em lugar
nenhum deste arquivo.

**Streaming (item 20)**: o JARVIS não tem streaming token-a-token real nesta
versão — `AIProvider.execute()` é atômico, devolve o resultado completo ou
levanta uma exceção; nunca emite conteúdo parcial. Isso significa que a
garantia "nunca misturar duas respostas" já vale por construção: o loop só
avança para o próximo candidato ANTES de qualquer resultado com conteúdo
visível ser obtido — no instante em que `result.has_visible_content` é
`True`, o loop retorna imediatamente e nunca mais toca outro provider. Não
há um estado `visible_content_emitted` separado a manter porque o próprio
fluxo do loop (`return` assim que há sucesso) já impõe essa invariante — ver
`tests/test_provider_router_v14.py::StreamingSafetyTests` para o teste que
prova isso."""

import logging
from dataclasses import dataclass, field, replace

from services.providers.base import AIProvider
from services.providers.env_config import DEFAULT_PROVIDER_ORDER
from services.providers.exceptions import (
    EmptyProviderResponseError,
    FallbackExhaustedError,
    NoFreeModelAvailableError,
    NonRecoverableProviderError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRefusedError,
    RecoverableProviderError,
)
from services.providers.registry import ProviderRegistry
from services.providers.types import AIExecutionResult, ModelId, ProviderId, ProviderStatus, RouteDecision, RouteRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Candidate:
    provider_id: ProviderId
    model: ModelId


@dataclass
class RequestContext:
    """Estado transitório de UMA geração (v1.6.0).

    A auditoria da v1.6.0 confirmou que o JARVIS nunca teve flag global de
    recusa — não existia `self._refused`, `conversation_blocked` nem
    `safety_state` em lugar nenhum. `fallback_count` e `attempts` já eram
    variáveis locais de `execute()`, portanto já isoladas na prática.

    Este objeto existe para transformar "já está isolado porque são
    variáveis locais" em uma invariante EXPLÍCITA e testável. A diferença
    importa: uma variável local está isolada por acidente da implementação
    atual, e a primeira refatoração que a promover a atributo de instância
    reintroduz o bug em silêncio. Com o contexto nomeado, um teste consegue
    afirmar que cada request começa zerada, e um `self._` acidental fica
    visível na revisão.

    Nada aqui sobrevive ao fim de `execute()`, e nada aqui é compartilhado
    entre requests — inclusive requests concorrentes, que recebem instâncias
    distintas por serem criadas dentro da própria chamada.
    """

    visible_content_emitted: bool = False
    fallback_count: int = 0
    refusal: str | None = None
    safety_metadata: str | None = None
    provider_error: str | None = None
    attempts: list[str] = field(default_factory=list)

    def record_attempt(self, candidate: "_Candidate", category: str) -> None:
        self.attempts.append(f"{candidate.provider_id.value}/{candidate.model}: {category}")
        self.fallback_count += 1
        self.provider_error = category


class ProviderRouter:
    def __init__(self, registry: ProviderRegistry, *, provider_order: tuple[ProviderId, ...] = DEFAULT_PROVIDER_ORDER) -> None:
        self._registry = registry
        self._provider_order = provider_order

    # ------------------------------------------------------------------
    # Decisão de um único candidato (comportamento explícito, sem cadeia)
    # ------------------------------------------------------------------

    async def select(self, request: RouteRequest) -> RouteDecision:
        """Decisão pura — nunca faz chamada de rede. Usada quando
        `preferred_provider`/`preferred_model` pedem uma escolha explícita
        (sem exploração automática, ver `execute()`)."""
        provider = self._pick_provider(request)
        model = self._pick_model(provider, request)
        if request.free_only and model not in provider.free_models():
            raise NoFreeModelAvailableError(
                f"{provider.id.value} não tem um modelo gratuito compatível com este pedido."
            )
        reason = "preferred_model" if request.preferred_model else ("free_only" if request.free_only else "default")
        return RouteDecision(provider=provider.id, requested_model=model, reason=reason)

    # ------------------------------------------------------------------
    # Execução — com cadeia de fallback (v1.4.0)
    # ------------------------------------------------------------------

    async def execute(self, request: RouteRequest) -> AIExecutionResult:
        """Executa `request`, avançando pela cadeia de fallback quando um
        candidato falha de forma recuperável. Devolve o primeiro resultado
        com conteúdo visível confirmado (e confirmado gratuito, se
        `free_only`); levanta `FallbackExhaustedError` se todos os
        candidatos se esgotarem, ou propaga imediatamente qualquer erro
        não-recuperável."""
        if request.preferred_provider is not None or request.preferred_model is not None:
            # Escolha explícita: um candidato só, sem exploração automática
            # — quem pede um provider/modelo específico está no controle e
            # não espera que o router tente outra coisa por conta própria.
            return await self._execute_single(request)

        candidates = self._candidates(request)
        if not candidates:
            if request.free_only:
                raise NoFreeModelAvailableError("Nenhum provider configurado oferece rota gratuita.")
            raise ProviderNotConfiguredError(
                "Nenhum provider configurado — configure OPENROUTER_API_KEY (ou outro) no ambiente."
            )

        # Contexto NOVO a cada chamada (v1.6.0, item 41). Criado aqui dentro,
        # nunca em `self`: é isso que garante que uma recusa, um erro de
        # provider ou uma contagem de fallback de uma request não atravesse
        # para a próxima — nem para uma request concorrente, que executa com
        # a sua própria instância.
        context = RequestContext()

        for candidate in candidates:
            provider = self._registry.get(candidate.provider_id)
            assert provider is not None  # veio de configured_providers(), sempre existe

            try:
                result = await provider.execute(request, model=candidate.model)
            except NonRecoverableProviderError:
                # Nunca mascarado: propaga na hora, sem tentar o próximo
                # candidato (item 18).
                raise
            except RecoverableProviderError as exc:
                context.record_attempt(candidate, exc.CATEGORY)
                logger.info(
                    "%s/%s indisponível (%s); avançando na cadeia de fallback.",
                    candidate.provider_id.value,
                    candidate.model,
                    exc.CATEGORY,
                )
                continue
            # Qualquer OUTRA exceção (TypeError, AttributeError, um bug
            # nosso de verdade) NÃO é capturada por nenhum `except` acima —
            # atravessa o loop e propaga, exatamente como deve (item 18).

            if request.free_only:
                try:
                    self._verify_free_or_raise(result, provider)
                except NoFreeModelAvailableError:
                    context.record_attempt(candidate, "not_confirmed_free")
                    logger.info(
                        "%s/%s não confirmou custo zero; avançando.", candidate.provider_id.value, candidate.model
                    )
                    continue

            if result.refusal is not None and not result.has_visible_content:
                # v1.6.0, item 40 — recusa ENCERRA a cadeia, não avança nela.
                # Percorrer os próximos providers atrás de um mais permissivo
                # seria usar o fallback para contornar safety. Recusa é uma
                # decisão do modelo sobre este pedido, não uma falha de
                # disponibilidade.
                context.refusal = result.refusal
                context.safety_metadata = result.refusal
                logger.info(
                    "%s/%s recusou o pedido; a cadeia de fallback NÃO avança (item 40).",
                    candidate.provider_id.value,
                    candidate.model,
                )
                raise ProviderRefusedError(reason=result.refusal)

            if not result.has_visible_content:
                # v1.6.0, item 7: raciocínio interno JAMAIS é promovido a
                # conteúdo visível por falta de outra coisa. Uma resposta que
                # só tem `reasoning` é uma resposta VAZIA, e é como vazia que
                # ela entra na decisão de fallback — a única saída aqui é
                # tentar o próximo candidato.
                context.record_attempt(candidate, "empty_response")
                logger.info(
                    "%s/%s respondeu sem conteúdo visível (reasoning=%d chars); avançando.",
                    candidate.provider_id.value,
                    candidate.model,
                    len(result.reasoning or ""),
                )
                continue

            # Sucesso real, com conteúdo visível confirmado: para aqui.
            # Nunca mais outro provider é chamado depois deste ponto — é
            # isso que garante que duas respostas nunca se misturam
            # (item 20). `visible_content_emitted` registra a invariante que
            # o `return` já impõe, para poder ser afirmada em teste.
            context.visible_content_emitted = True
            return replace(
                result,
                fallback_used=context.fallback_count > 0,
                fallback_count=context.fallback_count,
            )

        raise FallbackExhaustedError(
            f"Todos os {len(candidates)} candidatos configurados falharam.", attempts=context.attempts
        )

    async def _execute_single(self, request: RouteRequest) -> AIExecutionResult:
        decision = await self.select(request)
        provider = self._registry.get(decision.provider)
        assert provider is not None

        result = await provider.execute(request, model=decision.requested_model)
        if request.free_only:
            self._verify_free_or_raise(result, provider)
        return result

    # ------------------------------------------------------------------
    # Introspecção
    # ------------------------------------------------------------------

    def provider(self, provider_id: ProviderId) -> AIProvider | None:
        """Acesso ao provider instanciado — usado por quem precisa consultar
        `free_models()` diretamente (ex.: geração de título isolada). Leitura
        pura, sem rede."""
        return self._registry.get(provider_id)

    def configured_provider_ids(self) -> list[ProviderId]:
        """Providers com credencial presente (e habilitados), na ordem
        GLOBAL de fallback (item 14) — não a ordem de registro. Síncrono e
        sem rede — usado por `AIService.is_available()`, chamado com
        frequência (status do HUD)."""
        return [provider.id for provider in self._ordered_configured_providers()]

    async def health(self) -> dict[ProviderId, ProviderStatus]:
        result: dict[ProviderId, ProviderStatus] = {}
        for descriptor in self._registry.descriptors():
            provider = self._registry.get(descriptor.id)
            if provider is None:
                result[descriptor.id] = descriptor.status  # NOT_IMPLEMENTED — sem instância pra checar
                continue
            result[descriptor.id] = await provider.health()
        return result

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _ordered_configured_providers(self) -> list[AIProvider]:
        """Providers configurados, na ORDEM GLOBAL declarada em
        `env_config.DEFAULT_PROVIDER_ORDER` — não na ordem de registro do
        registry. Um provider configurado mas ausente da ordem declarada
        nunca é descartado: entra no fim, na ordem em que foi registrado."""
        by_id = {p.id: p for p in self._registry.configured_providers()}
        ordered = [by_id.pop(pid) for pid in self._provider_order if pid in by_id]
        ordered.extend(by_id.values())  # sobras não listadas na ordem global
        return ordered

    def _candidates(self, request: RouteRequest) -> list[_Candidate]:
        """Lista ACHATADA de (provider, modelo), na ordem em que devem ser
        tentados. "Próximo modelo do mesmo provider" e "próximo provider" são
        tratados de forma idêntica pelo loop de `execute()` — é só o próximo
        item desta lista."""
        providers = self._ordered_configured_providers()
        if request.free_only:
            providers = [p for p in providers if p.free_models()]

        candidates: list[_Candidate] = []
        for provider in providers:
            for model in provider.free_models():
                candidates.append(_Candidate(provider.id, model))
        return candidates

    def _pick_provider(self, request: RouteRequest) -> AIProvider:
        if request.preferred_provider is not None:
            provider = self._registry.get(request.preferred_provider)
            if provider is None:
                raise ProviderError(f"Provider '{request.preferred_provider.value}' não implementado nesta etapa.")
            if not provider.is_configured():
                raise ProviderNotConfiguredError(f"Provider '{request.preferred_provider.value}' sem API key configurada.")
            return provider

        candidates = self._ordered_configured_providers()
        if request.free_only:
            candidates = [p for p in candidates if p.free_models()]
        if not candidates:
            if request.free_only:
                raise NoFreeModelAvailableError("Nenhum provider configurado oferece rota gratuita.")
            raise ProviderNotConfiguredError("Nenhum provider configurado — configure OPENROUTER_API_KEY (ou outro) no ambiente.")
        return candidates[0]

    @staticmethod
    def _pick_model(provider: AIProvider, request: RouteRequest) -> ModelId:
        if request.preferred_model:
            return request.preferred_model
        if request.free_only:
            free = provider.free_models()
            if not free:
                raise NoFreeModelAvailableError(f"{provider.id.value} não declara nenhum modelo gratuito.")
            return free[0]
        # Sem `preferred_model` e sem `free_only`: nunca inventamos um
        # modelo pago padrão.
        free = provider.free_models()
        if free:
            return free[0]
        raise ProviderError(
            f"{provider.id.value} não tem modelo padrão nesta etapa — passe RouteRequest.preferred_model."
        )

    @staticmethod
    def _verify_free_or_raise(result: AIExecutionResult, provider: AIProvider) -> None:
        """Confirma gratuidade de duas formas possíveis (item 15):

        1. **Custo real reportado** (só a OpenRouter, via `usage.cost`):
           `cost.is_free is True` confirma; `cost.is_free is False` é veto
           explícito, nunca ignorado mesmo que `served_model` pareça grátis.
        2. **Allowlist** (todos os demais, que nunca reportam custo por
           chamada): `served_model` batendo com `provider.free_models()` —
           a garantia aqui é que TODA a lista já foi curada/validada como
           gratuita, então ecoar de volta um modelo dessa lista é prova
           suficiente."""
        cost = result.cost
        served = result.served_model

        if cost is not None and cost.is_free is False:
            raise NoFreeModelAvailableError(
                f"{provider.id.value} cobrou por esta chamada (served_model={served!r}, "
                f"cost={cost.amount} {cost.currency}) — bloqueado."
            )

        confirmed_free = (cost is not None and cost.is_free is True) or (
            served is not None and (served in provider.free_models() or served.endswith(":free"))
        )
        if not confirmed_free:
            logger.warning(
                "free_only=True mas a resposta de %s não confirma custo zero (served_model=%r, cost=%r) — sinalizando.",
                provider.id.value,
                served,
                cost,
            )
            raise NoFreeModelAvailableError(
                f"Não foi possível confirmar que a rota servida por {provider.id.value} era gratuita "
                f"(served_model={served!r}, cost={cost!r})."
            )
