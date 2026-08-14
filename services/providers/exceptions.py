"""Exceções do Provider Router — sempre com mensagem segura para log/HUD,
nunca incluindo uma credencial (ver `services/providers/secrets.py`)."""


class ProviderError(Exception):
    """Base de todo erro do Provider Router."""


class ProviderNotConfiguredError(ProviderError):
    """Provider sem API key no ambiente. Nunca é um erro de rede."""


class ProviderNotImplementedError(ProviderError):
    """Provider existe só como entrada de registry (ex.: Groq, Gemini) —
    ainda sem classe real nesta etapa."""


class ProviderUnavailableError(ProviderError):
    """Erro de rede, timeout, ou resposta 5xx do provider."""


class RateLimitedError(ProviderUnavailableError):
    """Resposta 429 do provider."""


class NoFreeModelAvailableError(ProviderError):
    """`RouteRequest.free_only=True` e nenhuma rota gratuita pôde ser
    garantida — nunca cai silenciosamente para um modelo pago. Código
    estável para quem quiser tratar isso programaticamente:
    `NoFreeModelAvailableError.CODE`."""

    CODE = "NO_FREE_MODEL_AVAILABLE"

    def __init__(self, message: str = "Nenhuma rota gratuita disponível.") -> None:
        super().__init__(f"{self.CODE}: {message}")


class EmptyProviderResponseError(ProviderError):
    """O provider respondeu com sucesso e metadata válida, mas **sem conteúdo
    visível** (v1.3.2).

    Acontece de verdade: um modelo de raciocínio pode gastar todo o orçamento
    de tokens no `reasoning` e devolver `content: null` com
    `finish_reason: "length"` — capturado em `tests/fixtures_openrouter.py`.

    Existe como erro próprio (e não como "resposta vazia") porque a diferença
    importa: metadata **nunca** pode ser persistida como mensagem de
    `assistant`, e o chamador precisa poder distinguir "o modelo não
    respondeu" de "o modelo respondeu string vazia de propósito"."""

    CODE = "EMPTY_PROVIDER_RESPONSE"

    def __init__(self, message: str = "O provider respondeu sem conteúdo visível.") -> None:
        super().__init__(f"{self.CODE}: {message}")
