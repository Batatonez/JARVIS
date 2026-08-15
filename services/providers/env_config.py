"""Configuração central do multi-provider (v1.4.0) — a "UMA configuração
central" pedida para a ordem de fallback e para o override de listas de
modelo.

Lê o ambiente diretamente com `os.environ`, no mesmo padrão de
`OpenRouterProvider` desde a v1.0 (nunca importa `config.settings` — o
pacote `services/providers/` é deliberadamente independente do resto do
projeto, para poder ser testado sem carregar o app inteiro)."""

import os

from services.providers.types import ProviderId

# Ordem GLOBAL de fallback (item 14). Um provider configurado mas ausente
# desta tupla nunca é descartado — `ProviderRouter` só o coloca no fim, na
# ordem em que foi registrado (nunca silenciosamente ignorado).
DEFAULT_PROVIDER_ORDER: tuple[ProviderId, ...] = (
    ProviderId.OPENROUTER,
    ProviderId.NVIDIA,
    ProviderId.GEMINI,
    ProviderId.GROQ,
    ProviderId.CEREBRAS,
    ProviderId.MISTRAL,
)


def env_flag(name: str, default: bool) -> bool:
    """Mesma tolerância de `config.settings._env_flag` (vazio/ausente ->
    default; aceita 1/0, true/false, yes/no, on/off) — reimplementada aqui
    em vez de importada para `services/providers/` continuar sem depender
    de `config/`."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def provider_enabled(env_var_name: str) -> bool:
    """`JARVIS_<PROVIDER>_ENABLED` — item 4. Ausente/vazio = habilitado por
    padrão; só `0`/`false`/`no`/`off` desativa."""
    return env_flag(env_var_name, True)


def resolve_model_override(env_var_name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    """`JARVIS_<PROVIDER>_MODELS=model1,model2,...` — itens 9/10/11/12/13.

    Regras aplicadas literalmente: separa por vírgula, remove espaço nas
    pontas, ignora entrada vazia, **preserva a ordem exatamente como
    fornecida** (nunca ordena, nunca deduplica, nunca remove um modelo
    "por preferência interna" — só a entrada vazia é descartada, porque não
    é um modelo, é ausência de um)."""
    raw = os.environ.get(env_var_name, "")
    if not raw.strip():
        return defaults
    parsed = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parsed or defaults
