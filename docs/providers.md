# Provider Router

> **Status: fundação aditiva (pré-v1.0), não conectada ao `JarvisCore`/`JarvisApplication` ainda.** Nada neste documento altera o comportamento do JARVIS estável (v0.9) — ver `docs/architecture.md` para a arquitetura em produção hoje. Este é o primeiro passo de uma etapa futura de multi-provider, deliberadamente isolada.

## Por que existe

O JARVIS quer, no futuro, decidir sozinho **provider, modelo, custo e fallback** — nunca delegar essa decisão para uma ferramenta de coordenação de agentes. O motivo é concreto: o Ruflo (`ruflo`/`@claude-flow`, ver `docs/ruflo-integration.md`) tem um bug confirmado de model routing em `agent_execute` — ele pode registrar `provider`/`openrouterModel` corretamente no momento do spawn, mas o executor real ignora os dois campos e sempre resolve para um ID nativo da Anthropic, mesmo quando o pedido era uma rota gratuita. Por isso: **`ProviderRouter` é a única autoridade sobre provider/modelo/custo no JARVIS. O Ruflo nunca decide isso.**

```
JARVIS
│
├── Provider Router (services/providers/)     ← autoridade sobre provider/modelo/custo
│   ├── OpenRouter                             implementado nesta etapa
│   ├── Groq / Gemini / Mistral / NVIDIA       registry apenas (NOT_IMPLEMENTED)
│   └── Anthropic                              arquitetura existente (services/ai_service.py) — não conectada aqui ainda
│
├── Ruflo Integration (services/providers/ruflo_coordinator.py)
│   └── swarm / coordenação / roles / memory  ← nunca decide modelo (ver docs/ruflo-integration.md)
│
└── AI Service (services/ai_service.py)        ← inalterado, continua servindo JarvisCore hoje
```

## Módulos

| Arquivo | Responsabilidade |
|---|---|
| `services/providers/types.py` | Tipos normalizados: `ProviderId`, `ModelId` (= `str`), `ProviderStatus`, `ModelCapability`, `RouteRequest`, `RouteDecision`, `UsageInfo`, `CostInfo`, `AIExecutionResult`, `ProviderDescriptor` |
| `services/providers/exceptions.py` | `ProviderError` e subclasses (`ProviderNotConfiguredError`, `ProviderUnavailableError`, `RateLimitedError`, `NoFreeModelAvailableError`) |
| `services/providers/base.py` | `AIProvider` — interface que todo provider concreto implementa |
| `services/providers/openrouter_provider.py` | `OpenRouterProvider` — único provider real nesta etapa |
| `services/providers/registry.py` | `ProviderRegistry` — inventário; `build_default_registry()` |
| `services/providers/router.py` | `ProviderRouter` — `select()`/`execute()`/`health()` |
| `services/providers/secrets.py` | `mask_secret()` — nunca expor uma API key inteira |
| `services/providers/ruflo_coordinator.py` | `RufloCoordinator` — fronteira de coordenação, ver `docs/ruflo-integration.md` |

## `ProviderRouter`

```python
from services.providers import ProviderRouter, RouteRequest, build_default_registry

router = ProviderRouter(build_default_registry())
decision = await router.select(RouteRequest(prompt="oi", free_only=True))
result = await router.execute(RouteRequest(prompt="oi", free_only=True))
```

- `select(request) -> RouteDecision` — decisão pura, **nunca** faz chamada de rede. Escolhe provider (respeitando `preferred_provider` se dado) e modelo (respeitando `preferred_model` se dado).
- `execute(request) -> AIExecutionResult` — chama `select()`, delega ao provider, e (quando `free_only=True`) verifica a resposta antes de devolver como sucesso.
- `health() -> dict[ProviderId, ProviderStatus]` — status de cada provider conhecido, incluindo os `NOT_IMPLEMENTED`.

## `openrouter/free` e o modo `free_only`

Suporte explícito ao slug `openrouter/free` (`services/providers/openrouter_provider.py::FREE_MODEL`). Quando `RouteRequest.free_only=True`:

1. **Antes da chamada** (`select()`): o modelo escolhido só pode ser um dos declarados em `provider.free_models()` — nunca um modelo pago é sequer solicitado.
2. **Depois da chamada** (`execute()`): a resposta é conferida contra o que o provider *relatou* — `served_model` (o modelo que a OpenRouter de fato usou, do campo `data["model"]` da resposta) e `cost` (normalizado de `data["usage"]["cost"]`, quando presente). Se `cost.is_free` vier `False`, ou se nem `cost` nem `served_model` confirmarem uma rota gratuita, `execute()` levanta `NoFreeModelAvailableError` (código `NO_FREE_MODEL_AVAILABLE`) — mesmo que a chamada já tenha sido feita.

**Limitação honesta**: a camada 2 é detecção, não prevenção total — a chamada HTTP já aconteceu antes de sabermos o custo real (a OpenRouter só devolve custo *depois* de processar a requisição). A camada 1 (nunca solicitar um modelo fora de `free_models()`) é a defesa principal contra gastar dinheiro; a camada 2 existe para nunca **aceitar silenciosamente** uma resposta que pareça ter sido servida por uma rota paga.

`model contém a palavra "free"` nunca é tratado como prova de custo zero — só `served_model` batendo com `free_models()`/sufixo `:free`, ou `cost.is_free is True`, contam.

## `AIExecutionResult` — `requested_model` vs. `served_model`

Nunca copiamos `requested_model` para `served_model` "por conveniência". `served_model` só é preenchido com o que a resposta de fato trouxe; pode ser `None` se a resposta não informar, ou diferente do que foi pedido (o provider redirecionou para outra rota) — nesses casos, `free_only` trata como não-verificado.

## Secrets

`OPENROUTER_API_KEY` é lida do ambiente em runtime (`os.environ.get`), nunca de um arquivo versionado, nunca de um argumento de CLI logado. Sem a variável: `OpenRouterProvider.is_configured() == False`, e o registry reporta `ProviderStatus.NOT_CONFIGURED` — o JARVIS continua funcionando normalmente (nenhuma exceção na inicialização, só ao tentar `execute()`). `services/providers/secrets.py::mask_secret()` é a única forma sancionada de mostrar "está configurado" em debug — nunca a chave inteira, no máximo os últimos 4 caracteres.

`.env.example` deveria documentar `OPENROUTER_API_KEY=` (vazio) — **não foi possível editá-lo nesta sessão**: tanto o harness quanto o `.claude/settings.json` instalado pelo Ruflo (`permissions.deny: ["Read(./.env)", "Read(./.env.*)"]`) bloquearam a leitura de `.env.example` por todas as ferramentas disponíveis (Read, Bash, PowerShell, Grep). Ver `docs/ruflo-integration.md`, seção "Efeito colateral encontrado". Adicione manualmente uma linha `OPENROUTER_API_KEY=` a `.env.example` quando conveniente.

## Registry / providers planejados

`ProviderRegistry.descriptors()` lista os 6 `ProviderId` conhecidos:

| Provider | Status nesta etapa |
|---|---|
| `openrouter` | `AVAILABLE` (com `OPENROUTER_API_KEY`) ou `NOT_CONFIGURED` |
| `groq` | `NOT_IMPLEMENTED` |
| `gemini` | `NOT_IMPLEMENTED` |
| `mistral` | `NOT_IMPLEMENTED` |
| `nvidia` | `NOT_IMPLEMENTED` |
| `anthropic` | `NOT_IMPLEMENTED` *neste router* — a integração real existente (`services/ai_service.py`/`ClaudeAgentProvider`) continua servindo `JarvisCore` normalmente; só não está conectada a este `ProviderRouter` ainda |

Nenhum dos providers planejados tem classe/import associado — só a entrada de inventário, para o HUD/CLI poderem mostrar "o que existe vs. o que está planejado" sem `if`s espalhados pelo projeto.

## Transporte HTTP

`OpenRouterProvider` usa `urllib.request` (biblioteca padrão) dentro de `loop.run_in_executor` — mesma escolha de `services/vosk_model_manager.py`, para não somar `requests`/`httpx`/`aiohttp` como dependência nova. O transporte é injetável (`HttpTransport`), por isso os testes nunca tocam rede de verdade.

## Testes

`tests/test_provider_router.py` (22 testes) e `tests/test_ruflo_coordinator.py` (4 testes) — 100% offline, sem nenhuma requisição real. Cobrem: key ausente, provider configurado, seleção `openrouter/free`, normalização de resposta/usage/custo, erro de rede, rate limit (429), erro de servidor (5xx), provider não configurado, free-only recusando resposta não confirmada como grátis (dois cenários: `served_model` pago sem custo, e custo > 0 explícito), registry (implementado vs. planejado), nenhum secret em log/corpo da requisição, e cancelamento (`asyncio.Task.cancel()` em pleno voo).

## O que esta etapa **não** faz

- Não conecta `ProviderRouter` a `JarvisCore`/`JarvisApplication`/`Orchestrator` — é aditivo, isolado, só testado diretamente.
- Não implementa Groq/Gemini/Mistral/NVIDIA de verdade — só registry.
- Não altera a integração Anthropic existente.
- Não faz nenhuma chamada real durante os testes nem durante esta implementação (ver `docs/ruflo-integration.md` para o smoke test manual opcional, que exige autorização explícita antes de rodar).
- Não implementa multiagente paralelo real (`jarvis-coordinator`/`jarvis-architect`/`jarvis-coder`/`jarvis-tester`/`jarvis-reviewer` cada um escolhendo modelo via `ProviderRouter`) — a arquitetura já suporta isso (`RouteRequest` é por-chamada, sem estado global), mas a integração de verdade é trabalho futuro.
