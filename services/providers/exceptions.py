"""Exceções do Provider Router — sempre com mensagem segura para log/HUD,
nunca incluindo uma credencial (ver `services/providers/secrets.py`).

**v1.4.0 — taxonomia recuperável vs. não-recuperável.** Com múltiplos
providers/modelos em cadeia, o router precisa de uma resposta estrutural
para "essa falha pode tentar o próximo candidato, ou é um sinal de que algo
está quebrado do nosso lado?":

    RecoverableProviderError     -> o router PODE avançar para o próximo
                                     provider/modelo (rate limit, timeout,
                                     erro de conexão, 5xx, modelo/capacidade
                                     indisponível, modelo inexistente)
    NonRecoverableProviderError  -> o router NUNCA avança silenciosamente —
                                     propaga na hora (401/403/400, request
                                     inválido, resposta malformada). Mascarar
                                     um destes com o próximo provider
                                     esconderia um bug/config quebrada nosso.

Nenhum destes é pego por um `except Exception` genérico em lugar nenhum do
router — cada categoria é uma classe própria, e um erro de verdade do JARVIS
(TypeError, AttributeError, bug de parsing) nunca é filho de `ProviderError`,
então atravessa o loop de fallback sem ser interceptado."""


class ProviderError(Exception):
    """Base de todo erro do Provider Router."""

    CATEGORY = "INTERNAL_ERROR"


class ProviderNotConfiguredError(ProviderError):
    """Provider sem API key no ambiente (ou desativado via
    `JARVIS_<PROVIDER>_ENABLED=0`). Nunca é um erro de rede — decidido antes
    de qualquer chamada, na seleção de candidatos."""

    CATEGORY = "NOT_CONFIGURED"


class ProviderNotImplementedError(ProviderError):
    """Provider existe só como entrada de registry — sem classe real ainda."""

    CATEGORY = "NOT_IMPLEMENTED"


# ----------------------------------------------------------------------
# Recuperáveis — o router pode avançar para o próximo candidato da cadeia.
# ----------------------------------------------------------------------


class RecoverableProviderError(ProviderError):
    """Marcador: falha transitória ou específica de UM candidato. Nunca
    intercepta um erro que não seja explicitamente desta família — não existe
    `except Exception` em lugar nenhum do loop de fallback."""


class ProviderUnavailableError(RecoverableProviderError):
    """Erro de rede genérico, ou resposta 5xx do provider."""

    CATEGORY = "PROVIDER_UNAVAILABLE"


class RateLimitedError(ProviderUnavailableError):
    """Resposta 429 do provider."""

    CATEGORY = "RATE_LIMITED"


class ProviderTimeoutError(ProviderUnavailableError):
    """A chamada não respondeu dentro do timeout configurado
    (`RouteRequest.timeout_s`, derivado de `Settings.provider_timeout_s`)."""

    CATEGORY = "TIMEOUT"


class ProviderConnectionError(ProviderUnavailableError):
    """Falha ao estabelecer conexão — DNS, recusa de conexão, rede
    indisponível. Diferente de timeout: aqui a conexão nem chegou a existir."""

    CATEGORY = "CONNECTION_ERROR"


class CapacityExhaustedError(ProviderUnavailableError):
    """Capacidade/quota esgotada para esta conta — inclui HTTP 402 (`payment
    required`), comum em contas de teste sem billing habilitado num tier que
    deixou de ser gratuito. Recuperável: outro provider pode ter quota."""

    CATEGORY = "CAPACITY_EXHAUSTED"


class ModelUnavailableError(ProviderUnavailableError):
    """Modelo existe, mas está temporariamente fora do ar (ex.: provider
    sinaliza manutenção/overload específico do modelo, não da conta toda)."""

    CATEGORY = "MODEL_UNAVAILABLE"


class ModelNotFoundError(ProviderUnavailableError):
    """HTTP 404 num endpoint de chat completions — quase sempre significa
    "esse model ID não existe/não está provisionado para esta conta"
    (confirmado na prática: `moonshotai/kimi-k2.6` na NVIDIA devolve
    exatamente isto). Recuperável: avança para o PRÓXIMO MODELO — o loop de
    fallback trata isso igual a qualquer outro recuperável, porque a lista de
    candidatos já é (provider, modelo) achatada em ordem; o "próximo modelo
    do mesmo provider" é automaticamente o próximo item da lista quando
    existir."""

    CATEGORY = "MODEL_NOT_FOUND"


# ----------------------------------------------------------------------
# Não-recuperáveis — o router NUNCA avança silenciosamente.
# ----------------------------------------------------------------------


class NonRecoverableProviderError(ProviderError):
    """Marcador: a causa é uma configuração/credencial quebrada ou um
    request inválido — nunca um problema transitório do provider. Deixar o
    router "resolver" isso tentando o próximo provider esconderia
    exatamente o tipo de bug que precisa aparecer."""


class AuthenticationError(NonRecoverableProviderError):
    """HTTP 401 — a API key está presente, mas foi rejeitada. Diferente de
    `ProviderNotConfiguredError` (que significa "sem key nenhuma"): aqui HÁ
    uma credencial, e ela está errada/expirada/revogada."""

    CATEGORY = "AUTH_ERROR"


class ProviderPermissionError(NonRecoverableProviderError):
    """HTTP 403 por autorização real (a credencial é válida, mas não tem
    permissão para este recurso) — não confundir com um 403 de bloqueio de
    borda/WAF, que `http_support.py` trata como `ProviderConnectionError`
    antes de chegar aqui."""

    CATEGORY = "PERMISSION_ERROR"


class BadRequestError(NonRecoverableProviderError):
    """HTTP 400/422 — o request que o JARVIS montou é inválido segundo o
    provider. Sempre um bug nosso (payload malformado), nunca do usuário."""

    CATEGORY = "BAD_REQUEST"


class ProviderValidationError(NonRecoverableProviderError):
    """Falha de validação local antes mesmo de enviar o request (ex.:
    parâmetro fora do intervalo aceito) — detectada pelo próprio provider
    Python antes de tocar rede."""

    CATEGORY = "VALIDATION_ERROR"


class InvalidProviderResponseError(NonRecoverableProviderError):
    """A resposta teve HTTP 2xx, mas o corpo não tem o formato esperado
    (JSON inválido, ou faltam campos estruturais mínimos). Isso é sinal de
    schema incompatível — precisa de correção de código, não de um provider
    diferente."""

    CATEGORY = "INVALID_PROVIDER_RESPONSE"


# ----------------------------------------------------------------------
# Especiais — tratados pelo próprio `ProviderRouter`, não pelos providers.
# ----------------------------------------------------------------------


class NoFreeModelAvailableError(ProviderError):
    """`RouteRequest.free_only=True` e esta resposta não confirma rota
    gratuita — nunca cai silenciosamente para um modelo pago. Recuperável NO
    NÍVEL DO ROUTER (tenta o próximo candidato da cadeia); só propaga para
    quem chamou quando NENHUM candidato confirma gratuidade."""

    CODE = "NO_FREE_MODEL_AVAILABLE"
    CATEGORY = "NO_FREE_MODEL_AVAILABLE"

    def __init__(self, message: str = "Nenhuma rota gratuita disponível.") -> None:
        super().__init__(f"{self.CODE}: {message}")


class EmptyProviderResponseError(ProviderError):
    """O provider respondeu com sucesso e metadata válida, mas **sem conteúdo
    visível** (v1.3.2) — ex.: um modelo de raciocínio gastou o orçamento de
    tokens inteiro em `reasoning` e devolveu `content: null`. Recuperável NO
    NÍVEL DO ROUTER, igual a `NoFreeModelAvailableError`."""

    CODE = "EMPTY_PROVIDER_RESPONSE"
    CATEGORY = "EMPTY_PROVIDER_RESPONSE"

    def __init__(self, message: str = "O provider respondeu sem conteúdo visível.") -> None:
        super().__init__(f"{self.CODE}: {message}")


class FallbackExhaustedError(ProviderError):
    """Todos os candidatos (provider, modelo) da cadeia de fallback foram
    tentados e nenhum produziu uma resposta utilizável — nunca um erro
    silencioso: carrega `attempts`, a lista legível de cada tentativa e por
    que falhou (sem nenhuma credencial dentro)."""

    CODE = "FALLBACK_EXHAUSTED"
    CATEGORY = "FALLBACK_EXHAUSTED"

    def __init__(
        self, message: str = "Todos os providers/modelos configurados falharam.", *, attempts=None
    ) -> None:
        self.attempts: tuple[str, ...] = tuple(attempts or ())
        detail = f" Tentativas: {'; '.join(self.attempts)}." if self.attempts else ""
        super().__init__(f"{self.CODE}: {message}{detail}")
