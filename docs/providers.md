# Provider Router

> **Status (v1.0): conectado ao fluxo real do aplicativo.** Com `OPENROUTER_API_KEY` no ambiente, toda conversa do JARVIS passa por aqui:
> ```
> JarvisApplication → Orchestrator → AIService → ProviderRouter → OpenRouterProvider → modelo
> ```
> O adaptador entre a interface `AIService` (que o Orchestrator já conhecia desde a v0.3) e o router é `services/provider_ai_service.py::ProviderRouterAIService`. Nenhuma camada acima dele precisou mudar.

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
| `services/provider_ai_service.py` | `ProviderRouterAIService` — adapta `AIService` ao router (v1.0) |
| `services/context_builder.py` | Sanitização + truncagem do contexto que sai da máquina (v1.0) |

## Sessão de conversa sobre uma API stateless (v1.0)

A API da OpenRouter é **stateless**: ao contrário do Claude Agent SDK (que mantém a sessão do lado dele), aqui o contexto precisa ser reenviado a cada chamada. `ProviderRouterAIService` resolve isso guardando o histórico da sessão em RAM e reenviando os últimos turnos via `RouteRequest.history` — é o que transforma requisições isoladas na "sessão contínua" que o resto do JARVIS espera do contrato `start`/`ask`/`close`.

Há um teto de turnos reenviados (`max_history_messages`, padrão 20): sem ele, o custo e a latência de cada chamada cresceriam indefinidamente numa conversa longa.

O `system_prompt` combina a identidade de runtime (`services/runtime_identity.py`) com a memória do usuário já **sanitizada e truncada** por `JarvisCore.build_memory_context()` — ver `docs/security.md`, seção "Privacidade ao chamar a IA".

## Orçamento de tokens

`JARVIS_PROVIDER_MAX_TOKENS` (padrão **1024**). O padrão não é acidental: no smoke test manual da v1.0, com um orçamento de 32 tokens, a rota `openrouter/free` foi servida por `nvidia/nemotron-nano-9b-v2:free` — um modelo de raciocínio que consumiu os 32 tokens inteiros em raciocínio interno e devolveu `content: ""`. A v1.0 trata resposta vazia como **falha explícita** (nunca persiste uma mensagem vazia como se fosse resposta válida) e usa um orçamento realista por padrão.

## Streaming — por que não nesta versão

O HUD já tem o contrato de eventos (`response.started`/`delta`/`completed`/`failed`) e o `MessageListModel.update_content()` pronto. Ainda assim, a v1.0 entrega **resposta completa**, deliberadamente: streaming honesto exigiria trocar o transporte (`urllib` bloqueante em executor) por um cliente que consuma SSE incrementalmente, propagar deltas por `Orchestrator`/`AIService` (cujo contrato hoje é `ask() -> str`), e refazer a interação com cancelamento. Numa versão cujo objetivo é **estabilização**, isso é risco desproporcional.

O que **não** foi feito: nenhum "streaming falso" (quebrar a resposta pronta em pedaços e emitir deltas fingindo progresso). Streaming real é a próxima evolução.

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

## `openrouter/free` não é mais a rota padrão (v1.3.2)

**Bug real, causa raiz provada.** Perguntando "Opa! E aí, tudo bem?", a resposta visível do JARVIS às vezes era literalmente:

```text
User Safety: safe
```

`openrouter/free` é um **agregador cego**: sorteia qualquer modelo do pool gratuito a cada chamada. Nesse pool está `nvidia/nemotron-3.5-content-safety:free`, que **não é um modelo de conversa** — é um classificador de conteúdo. A resposta RAW capturada (em `tests/fixtures_openrouter.py`) é:

```json
{"role": "assistant", "content": "User Safety: safe", "refusal": null, "reasoning": "..."}
```

Ou seja: **o parser estava certo, a seleção estava errada.** O classificador respondeu exatamente o que ele existe para responder; nós é que perguntamos a ele.

A correção é estrutural, em duas frentes:

1. `free_models()` passou a devolver `FREE_CHAT_MODELS` — uma lista **curada** de modelos gratuitos de chat. A metadata da API não distingue um classificador de um modelo de conversa (ambos declaram `text -> text`), então não há filtro automático possível: a curadoria é manual e precisa ser revisada quando o pool mudar.
2. `AGGREGATE_FREE_MODEL` (`openrouter/free`) continua na lista, **no fim** — é uma rota gratuita legítima para quem a pedir por `preferred_model`, mas nunca é a escolha automática.

Nenhum filtro de texto foi usado. Um filtro do tipo `if "User Safety" in content` quebraria uma resposta legítima que explique o formato de um classificador — há teste para exatamente isso (`NotABlacklistTests`).

## Resposta separada por natureza (v1.3.2)

`ProviderMessage` (`services/providers/types.py`) separa o que a API entrega no mesmo objeto `message`:

| campo | destino |
|---|---|
| `visible_content` | pode virar mensagem de `assistant` |
| `reasoning` | raciocínio interno — **nunca** UI, nunca TTS, nunca persistido |
| `refusal` | recusa estruturada — diagnóstico |
| `usage` / `cost` / `served_model` | metadata — nunca entra no texto |

Isso importa porque `content` pode vir `null` **com HTTP 200 e usage contabilizado**: um modelo de raciocínio gasta todo o orçamento pensando e não escreve nada. Capturado de verdade em `poolside/laguna-xs-2.1:free`, com `finish_reason: "length"`.

Nesse caso o serviço levanta `EmptyProviderResponseError` (código `EMPTY_PROVIDER_RESPONSE`) e faz **UM** retry com o próximo modelo gratuito da lista — repetir com o mesmo modelo tenderia ao mesmo resultado. `free_only` continua valendo em toda tentativa; nada vazio é persistido; a mensagem do usuário não é duplicada.

## O modo `free_only`

Quando `RouteRequest.free_only=True`:

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

## O que a v1.0 **não** faz

- Não implementa Groq/Gemini/Mistral/NVIDIA de verdade — só registry `NOT_IMPLEMENTED`.
- Não roteia a integração Anthropic existente por este router (o `ClaudeAgentProvider` continua como caminho separado, escolhido em `create_ai_service()` quando não há `OPENROUTER_API_KEY`).
- Não faz streaming (acima).
- Não faz nenhuma chamada real durante os testes — o transporte HTTP é injetável e os testes usam um fake.
- Não implementa multiagente paralelo real (`jarvis-coordinator`/`jarvis-architect`/... cada um com seu modelo). A arquitetura suporta (`RouteRequest` é por-chamada, sem estado global), mas a integração é trabalho futuro.

## Smoke test manual real (validado)

Executado na v1.0, com autorização explícita, uma única chamada:

| Campo | Valor |
|---|---|
| Provider / modelo solicitado | `openrouter` / `openrouter/free` |
| Modelo **servido** | `nvidia/nemotron-nano-9b-v2:free` |
| HTTP | 200 |
| Custo relatado | `0.0 USD`, `is_free: true` |
| `free_only` respeitado | sim — a resposta só foi aceita porque `cost.is_free` era `True` |

Note que `served_model ≠ requested_model`: isso é **esperado** num roteador agregador. A prova de custo zero veio dos dados retornados (`usage.cost`), nunca do nome pedido.
