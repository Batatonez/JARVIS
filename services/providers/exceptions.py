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
