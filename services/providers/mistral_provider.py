"""`MistralProvider` — Mistral direto (`https://api.mistral.ai/v1`),
OpenAI-compatible. Último provider da cadeia (v1.4.0, item 13).

Lê `MISTRAL_API_KEY`. Desativável via `JARVIS_MISTRAL_ENABLED=0`. Modelos
sobrescrevíveis via `JARVIS_MISTRAL_MODELS=modelo1,modelo2`.

**Por que existe, apesar de a NVIDIA também servir modelos "mistralai/..."**:
infraestrutura e quota independentes — a `mistral-medium-3.5` que não existe
no catálogo da NVIDIA NIM (ver `nvidia_provider.py`) EXISTE aqui, direto na
API oficial da Mistral. São contas/limites diferentes; não é duplicação, é
uma segunda rota genuinamente independente.

--------------------------------------------------------------------------
CATÁLOGO — validado por chamada real (item 13: 1 ou 2 modelos)
--------------------------------------------------------------------------
    codestral-latest        AVAILABLE — ~520ms, conteúdo visível imediato.
                             Modelo de CÓDIGO dedicado da Mistral — pick
                             primário, corresponde à prioridade "coding".
    mistral-small-latest    AVAILABLE — ~540ms, conteúdo visível imediato.
                             Generalista rápido/barato — pick secundário.

(`devstral-latest` e `mistral-medium-latest` também responderam corretamente
nos testes reais, mas ficaram de fora dos 2 padrão pedidos — continuam
disponíveis via `JARVIS_MISTRAL_MODELS` para quem preferir.)
"""

from services.providers.openai_compatible import OpenAICompatibleProvider
from services.providers.types import ProviderId


class MistralProvider(OpenAICompatibleProvider):
    id = ProviderId.MISTRAL
    label = "Mistral"
    env_key_name = "MISTRAL_API_KEY"
    env_enabled_name = "JARVIS_MISTRAL_ENABLED"
    env_models_name = "JARVIS_MISTRAL_MODELS"
    base_url = "https://api.mistral.ai/v1"
    default_models = (
        "codestral-latest",
        "mistral-small-latest",
    )
