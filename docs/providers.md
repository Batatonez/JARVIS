# Provider Router

> **Status (v1.4.0): cadeia de fallback entre 6 providers.** Com qualquer uma das 6 API keys no ambiente, toda conversa do JARVIS passa por aqui:
> ```
> JarvisApplication → Orchestrator → AIService → ProviderRouter → OpenRouter → NVIDIA → Gemini → Groq → Cerebras → Mistral → modelo
> ```
> O adaptador entre a interface `AIService` (que o Orchestrator já conhecia desde a v0.3) e o router é `services/provider_ai_service.py::ProviderRouterAIService`. Nenhuma camada acima dele precisou mudar — a v1.4.0 moveu toda a lógica de ordem/retry/fallback para **dentro** do `ProviderRouter`, que continua sendo a única autoridade.

## Por que existe

O JARVIS decide sozinho **provider, modelo, ordem, fallback e custo** — nunca delega essa decisão para uma ferramenta de coordenação de agentes. O motivo é concreto: o Ruflo (`ruflo`/`@claude-flow`, ver `docs/ruflo-integration.md`) tem um bug confirmado de model routing em `agent_execute` — ele pode registrar `provider`/`openrouterModel` corretamente no momento do spawn, mas o executor real ignora os dois campos e sempre resolve para um ID nativo da Anthropic, mesmo quando o pedido era uma rota gratuita. Por isso: **`ProviderRouter` é a única autoridade sobre provider/modelo/ordem/fallback/custo no JARVIS. O Ruflo nunca decide isso — nem na v1.4.0, nem no HIGH mode planejado para a próxima versão** (que vai colocar Ruflo **acima** do router para coordenação de swarm, nunca escolhendo modelo por conta própria).

```
JARVIS
│
├── Provider Router (services/providers/)     ← autoridade sobre provider/modelo/ordem/fallback/custo
│   ├── OpenRouter    implementado desde a v1.0 — primeiro da cadeia
│   ├── NVIDIA NIM     ┐
│   ├── Gemini         │ implementados na v1.4.0 — cadeia de fallback
│   ├── Groq           │ (services/providers/router.py::DEFAULT_PROVIDER_ORDER
│   ├── Cerebras       │  em services/providers/env_config.py)
│   ├── Mistral direto ┘
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
| `services/providers/types.py` | Tipos normalizados: `ProviderId`, `ModelId` (= `str`), `ProviderStatus`, `ModelCapability`, `RouteRequest`, `RouteDecision`, `UsageInfo`, `CostInfo`, `ProviderMessage`, `AIExecutionResult`, `ProviderDescriptor` |
| `services/providers/exceptions.py` | `ProviderError` e a taxonomia recuperável/não-recuperável (v1.4.0) — ver seção própria abaixo |
| `services/providers/base.py` | `AIProvider` — interface que todo provider concreto implementa |
| `services/providers/env_config.py` | **v1.4.0** — `DEFAULT_PROVIDER_ORDER` (a "UMA configuração central" da ordem global), `resolve_model_override()`, `provider_enabled()` |
| `services/providers/http_support.py` | **v1.4.0** — transporte `urllib` compartilhado, `raise_for_status()` (classificação HTTP→exceção), `parse_openai_chat_message()` |
| `services/providers/openrouter_provider.py` | `OpenRouterProvider` — primeiro provider da cadeia |
| `services/providers/openai_compatible.py` | **v1.4.0** — `OpenAICompatibleProvider`, base reusada por NVIDIA/Groq/Cerebras/Mistral |
| `services/providers/nvidia_provider.py` | **v1.4.0** — `NvidiaProvider` (NVIDIA NIM) |
| `services/providers/gemini_provider.py` | **v1.4.0** — `GeminiProvider` (protocolo próprio, key na query string) |
| `services/providers/groq_provider.py` | **v1.4.0** — `GroqProvider` |
| `services/providers/cerebras_provider.py` | **v1.4.0** — `CerebrasProvider` |
| `services/providers/mistral_provider.py` | **v1.4.0** — `MistralProvider` (independente do Mistral servido via NVIDIA) |
| `services/providers/registry.py` | `ProviderRegistry` — inventário; `build_default_registry()` sempre registra os 6 |
| `services/providers/router.py` | `ProviderRouter` — `select()`/`execute()` (cadeia de fallback completa, v1.4.0)/`health()` |
| `services/providers/secrets.py` | `mask_secret()` — nunca expor uma API key inteira |
| `services/providers/ruflo_coordinator.py` | `RufloCoordinator` — fronteira de coordenação, ver `docs/ruflo-integration.md` |
| `services/provider_ai_service.py` | `ProviderRouterAIService` — adapta `AIService` ao router; a partir da v1.4.0 só cuida de sessão/histórico, todo fallback é do router |
| `services/context_builder.py` | Sanitização + truncagem do contexto que sai da máquina (v1.0) |
| `scripts/test_ai_providers.py` | **v1.4.0** — smoke test real, opt-in, nunca roda no `pytest` (ver seção própria) |

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
- `execute(request) -> AIExecutionResult` — **sem** `preferred_provider`/`preferred_model`: percorre a cadeia de fallback completa (v1.4.0, seção abaixo). **Com** um dos dois: um candidato só, sem exploração automática — quem pede explicitamente está no controle.
- `health() -> dict[ProviderId, ProviderStatus]` — status de cada provider conhecido, incluindo os `NOT_IMPLEMENTED`.

## Cadeia de fallback multi-provider (v1.4.0)

### Ordem global

Uma configuração central (`services/providers/env_config.py::DEFAULT_PROVIDER_ORDER`):

```text
1. OpenRouter
2. NVIDIA NIM
3. Gemini
4. Groq
5. Cerebras
6. Mistral (direto)
   → FALLBACK_EXHAUSTED
```

Dentro de cada provider, uma lista ordenada de modelos (própria de cada módulo — `OpenRouterProvider.FREE_CHAT_MODELS`, `NvidiaProvider.default_models`, etc.). `ProviderRouter._candidates()` achata tudo isso numa lista única de `(provider, modelo)`, na ordem: todos os modelos do provider 1, depois todos do provider 2, e assim por diante. "Próximo modelo do mesmo provider" e "próximo provider" são o mesmo conceito para o loop — só o próximo item da lista.

Um provider **desativado** (`JARVIS_<PROVIDER>_ENABLED=0`) ou **sem API key** simplesmente não entra na lista — `is_configured()` cobre os dois casos de forma idêntica, e o loop nunca sabe (nem precisa saber) qual dos dois motivos foi.

### Recuperável vs. não-recuperável

`services/providers/exceptions.py` define duas famílias:

| | Exemplos | O que o router faz |
|---|---|---|
| `RecoverableProviderError` | rate limit (429), timeout, conexão recusada, 5xx, capacidade esgotada (402/503), modelo indisponível, modelo não encontrado (404) | avança para o **próximo candidato** da lista |
| `NonRecoverableProviderError` | credencial rejeitada (401), sem permissão (403), request inválido (400/422), resposta fora do schema | propaga **imediatamente** — nunca tenta outro provider |

Qualquer exceção que não seja uma dessas (um `TypeError`/`AttributeError` nosso, por exemplo) **não é interceptada em lugar nenhum** — atravessa o loop de fallback intacta. Não existe `except Exception:` em `services/providers/router.py`; a diferença entre "não capturamos genericamente" e "capturamos e mascaramos" é o que garante que um bug do JARVIS nunca vira "olha, tentei outro provider e funcionou" silenciosamente.

Quando **todos** os candidatos se esgotam de forma recuperável, o router levanta `FallbackExhaustedError` (código `FALLBACK_EXHAUSTED`), com `.attempts` listando cada `provider/modelo: CATEGORIA` tentado — nunca um "algo deu errado" genérico.

### Classificação HTTP → categoria

`services/providers/http_support.py::raise_for_status()`, compartilhada por todos os 6 providers:

| HTTP | Categoria | Recuperável? |
|---|---|---|
| 401 | `AUTH_ERROR` | não |
| 403 | `PERMISSION_ERROR` | não |
| 400 / 422 | `BAD_REQUEST` | não |
| 402 | `CAPACITY_EXHAUSTED` | sim — confirmado na prática (Cerebras) |
| 404 | `MODEL_NOT_FOUND` | sim — confirmado na prática (NVIDIA, `kimi-k2.6`) |
| 408 | `TIMEOUT` | sim |
| 429 | `RATE_LIMITED` | sim |
| 503 | `CAPACITY_EXHAUSTED` | sim |
| outro 5xx | `PROVIDER_UNAVAILABLE` | sim |

Erros de transporte (sem resposta HTTP nenhuma) são classificados por `classify_transport_exception()`: `TimeoutError`/timeout do `urllib` → `TIMEOUT`; `urllib.error.URLError`/`OSError` → `CONNECTION_ERROR`. Ambos recuperáveis.

### Retries

No máximo **uma tentativa por candidato** — a lista de candidatos já é a política de retry: um modelo que falhou não é tentado de novo (repetir o mesmo modelo raramente muda o resultado; trocar de modelo/provider é o que resolve de verdade). O loop é limitado pelo tamanho finito da lista, então não há como entrar em looping infinito.

### Streaming — por que a garantia vale por construção

O JARVIS **não tem streaming token-a-token real** nesta versão (nem na v1.0-v1.3.2): `AIProvider.execute()` é atômico — devolve o resultado inteiro de uma vez, ou levanta uma exceção; nunca emite conteúdo parcial. Isso significa que a regra "nunca misturar duas respostas" vale **por construção**: o loop de `ProviderRouter.execute()` só avança para o próximo candidato quando o atual falhou ou devolveu resposta sem conteúdo visível — no instante em que `result.has_visible_content` é `True`, a função **retorna imediatamente**, e nenhum outro provider é tocado depois disso. Não existe um estado `visible_content_emitted` separado para gerenciar porque o próprio `return` já impõe essa invariante.

Testado explicitamente em `tests/test_provider_router_v14.py::StreamingSafetyTests` — inclusive um cenário com um terceiro candidato que levantaria erro se fosse chamado, provando que ele nunca é.

### Cancelamento

`asyncio.CancelledError` não é subclasse de `Exception` em Python 3.8+ — é `BaseException`. Como o loop de fallback só intercepta `RecoverableProviderError`/`NonRecoverableProviderError` (ambos `Exception`), um cancelamento **nunca** é capturado pelo loop: atravessa imediatamente, encerra a cadeia inteira, e nunca inicia outra tentativa. Nenhum código especial foi necessário — é uma consequência da hierarquia de exceções do próprio Python, testada em `tests/test_provider_router_v14.py::CancellationTests`.

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

1. **Antes da chamada** (seleção de candidatos): o modelo escolhido só pode ser um dos declarados em `provider.free_models()` — nunca um modelo pago é sequer solicitado.
2. **Depois da chamada** (`_verify_free_or_raise`): a resposta é conferida contra o que o provider *relatou*.

**v1.4.0 — free-only sem metadata de custo confiável (item 15).** Só a OpenRouter devolve custo real por chamada (`usage.cost`). NVIDIA, Gemini, Groq, Cerebras e Mistral **nunca** reportam quanto uma chamada custou — não há campo equivalente nas respostas delas. A garantia free-only para esses 5 é **allowlist pura**:

- todo provider ecoa `served_model` de volta igual ao modelo pedido (os 4 OpenAI-compatible fazem isso via `data.get("model") or model`; o Gemini, cuja resposta nem inclui esse campo, usa o `model` pedido diretamente);
- `_verify_free_or_raise` confirma gratuidade quando `served_model` bate com a lista curada de `free_models()` — a garantia é que **toda a lista já foi validada como gratuita antes de entrar no código** (ver a seção de catálogo abaixo), então ecoar de volta um modelo dela é prova suficiente.

**Isto é uma limitação documentada, não um "quase free-only"**: se um desses 5 providers começar a cobrar por um modelo que está na nossa lista **sem avisar via HTTP** (sem 402, sem erro nenhum, só descontando saldo silenciosamente), o JARVIS não teria como detectar isso pós-chamada — não existe o dado para verificar. A defesa real contra isso é a curadoria cuidadosa da lista + revisão periódica (o mesmo aviso já valia para `FREE_CHAT_MODELS` da OpenRouter desde a v1.3.2). Não tentamos "adivinhar" se um endpoint cobra por heurística nenhuma.

Para a OpenRouter especificamente, a camada 2 confere `cost.is_free`/`served_model` como sempre: se `cost.is_free` vier `False`, ou se nem `cost` nem `served_model` confirmarem uma rota gratuita, a verificação falha (recuperável — o router avança para o próximo candidato; só vira `NoFreeModelAvailableError` visível para quem chamou se TODOS os candidatos falharem assim).

`model contém a palavra "free"` nunca é tratado como prova de custo zero — só `served_model` batendo com `free_models()`/sufixo `:free`, ou `cost.is_free is True`, contam.

## Catálogo por provider (validado por chamada real, v1.4.0)

Todos os IDs abaixo foram confirmados batendo na API de verdade durante o desenvolvimento (`scripts/test_ai_providers.py` + probes pontuais) — nenhum foi presumido. Ver o comentário de cada módulo (`services/providers/<nome>_provider.py`) para o detalhe completo de cada descoberta.

| Provider | Modelo padrão (ordem) | Confirmado | Observação |
|---|---|---|---|
| NVIDIA | `nvidia/nemotron-3-ultra-550b-a55b` | ✅ | ~1,2s |
| NVIDIA | `z-ai/glm-5.2` | ✅ | **~73s** — frequentemente estoura o timeout padrão (60s) e cai para o próximo, por desenho |
| NVIDIA | `nvidia/nemotron-3-super-120b-a12b` | ✅ | ~0,7s |
| NVIDIA | `nvidia/nemotron-3-nano-30b-a3b` | ✅ | ~0,7s |
| ~~NVIDIA~~ | ~~`moonshotai/kimi-k2.6`~~ | ❌ 404 | não provisionado para esta conta — excluído |
| ~~NVIDIA~~ | ~~`mistralai/mistral-medium-3.5-128b`~~ | ❌ | não existe no catálogo NIM desta conta — excluído |
| Gemini | `gemini-3.5-flash` | ✅ | |
| Gemini | `gemini-3.5-flash-lite` | ✅ | |
| ~~Gemini~~ | ~~`gemini-2.5-pro`/`gemini-2.5-flash`~~ | ❌ 404 | "no longer available to new users" |
| ~~Gemini~~ | ~~`gemini-pro-latest`~~ | ❌ 429 | sem quota gratuita nesta conta |
| Groq | `llama-3.3-70b-versatile` | ✅ | ~390ms |
| Groq | `openai/gpt-oss-20b` | ✅ | reasoning-heavy |
| Cerebras | `gpt-oss-120b` | ⚠️ 402 | ver limitação abaixo |
| Cerebras | `zai-glm-4.7` | ⚠️ 402 | ver limitação abaixo |
| Mistral | `codestral-latest` | ✅ | ~520ms |
| Mistral | `mistral-small-latest` | ✅ | ~540ms |

**Limitação Cerebras**: os 3 modelos de chat desta conta (`gemma-4-31b`, `gpt-oss-120b`, `zai-glm-4.7`) devolvem HTTP 402 (`payment_required`) — nenhum pôde ser positivamente confirmado como utilizável de graça com esta key. Classificado como `CAPACITY_EXHAUSTED` (recuperável): a cadeia avança para o Mistral sem nunca oferecer pagamento automaticamente. A implementação está correta e outra conta Cerebras com quota gratuita usaria o mesmo código com sucesso.

DeepSeek não foi adicionado a nenhum provider: não foi pedido explicitamente, e o único catálogo que o expõe (NVIDIA) só tem `deepseek-coder-6.7b-instruct`/`deepseek-v4-flash-0731`, nenhum correspondendo a um pedido real.

## Variáveis de ambiente (v1.4.0)

Além de `OPENROUTER_API_KEY`/`JARVIS_FREE_ONLY`/`JARVIS_PROVIDER_MAX_TOKENS`/`JARVIS_PROVIDER_TIMEOUT_S` (já existentes desde a v1.0):

| Variável | Efeito | Default |
|---|---|---|
| `NVIDIA_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` / `CEREBRAS_API_KEY` / `MISTRAL_API_KEY` | credencial de cada provider | ausente = provider pulado |
| `JARVIS_NVIDIA_ENABLED` / `JARVIS_GEMINI_ENABLED` / `JARVIS_GROQ_ENABLED` / `JARVIS_CEREBRAS_ENABLED` / `JARVIS_MISTRAL_ENABLED` | liga/desliga o provider mesmo com key presente | `1` (habilitado) |
| `JARVIS_NVIDIA_MODELS` / `JARVIS_GEMINI_MODELS` / `JARVIS_GROQ_MODELS` / `JARVIS_CEREBRAS_MODELS` / `JARVIS_MISTRAL_MODELS` | sobrescreve a lista de modelos, `modelo1,modelo2,...` — ordem preservada exatamente, nunca ordenada/deduplicada | lista curada do módulo do provider |

`OPENROUTER_API_KEY` não tem uma variável `_ENABLED`/`_MODELS` equivalente nesta versão — continua controlada só pela presença da key (`FREE_CHAT_MODELS`, hardcoded, curado por revisão de código desde a v1.3.2).

**Timeout**: uma única variável (`JARVIS_PROVIDER_TIMEOUT_S`, padrão 60s) vale para todos os 6 providers — `urllib.urlopen(timeout=...)` já é um timeout combinado de conexão+leitura, então não há como (nem motivo para) separar em connect/read distintos sem trocar de biblioteca HTTP.

## `AIExecutionResult` — `requested_model` vs. `served_model`

Nunca copiamos `requested_model` para `served_model` "por conveniência". `served_model` só é preenchido com o que a resposta de fato trouxe; pode ser `None` se a resposta não informar, ou diferente do que foi pedido (o provider redirecionou para outra rota) — nesses casos, `free_only` trata como não-verificado.

## Secrets

Todas as 6 API keys são lidas do ambiente em runtime (`os.environ.get`), nunca de um arquivo versionado, nunca de um argumento de CLI logado. Sem uma variável: o provider correspondente reporta `is_configured() == False`/`ProviderStatus.NOT_CONFIGURED` — o JARVIS continua funcionando normalmente com o que estiver configurado (nenhuma exceção na inicialização, só ao tentar `execute()` sem candidato nenhum). `services/providers/secrets.py::mask_secret()` é a única forma sancionada de mostrar "está configurado" em debug — nunca a chave inteira, no máximo os últimos 4 caracteres.

`.env.example` documenta todas as 6 (`OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`) como placeholders vazios.

## Registry

`build_default_registry()` **sempre** registra os 6 providers reais — cada um decide sozinho se está `is_configured()` (key presente **e** habilitado). `ProviderRegistry.descriptors()`:

| Provider | Status |
|---|---|
| `openrouter` / `nvidia` / `gemini` / `groq` / `cerebras` / `mistral` | `AVAILABLE` (key presente e habilitado) ou `NOT_CONFIGURED` |
| `anthropic` | `NOT_IMPLEMENTED` *neste router* — a integração real existente (`services/ai_service.py`/`ClaudeAgentProvider`) continua servindo `JarvisCore` normalmente; só não está conectada a este `ProviderRouter` (fora de escopo, ver `docs/ruflo-integration.md`) |

## Transporte HTTP

Todos os 6 providers usam `urllib.request` (biblioteca padrão) dentro de `loop.run_in_executor` — mesma escolha de `services/vosk_model_manager.py`, para não somar `requests`/`httpx`/`aiohttp` como dependência nova. O transporte é compartilhado (`services/providers/http_support.py::urllib_transport`) e injetável (`HttpTransport`), por isso os testes nunca tocam rede de verdade.

**Achado real (v1.4.0)**: `urllib.request` sem um `User-Agent` explícito era bloqueado pela Cloudflare na frente da API da Groq (HTTP 403, "error code: 1010" — bloqueio por assinatura de user-agent, nada a ver com a API key). Corrigido com um `User-Agent` padrão (`DEFAULT_USER_AGENT`) aplicado a todos os requests.

## Testes

- `tests/test_provider_router.py` (22 testes) — comportamento da OpenRouter isolada, preservado desde a v1.0.
- `tests/test_provider_response_v132.py` (25 testes) — separação `visible_content`/`reasoning`/`refusal` (v1.3.2).
- `tests/test_provider_router_v14.py` (44 testes, v1.4.0) — a cadeia de fallback completa: os 40 cenários do escopo desta versão (sucesso sem fallback, cada tipo de erro recuperável avançando, 401/400 nunca mascarados, exaustão de modelo→exaustão de provider→`FALLBACK_EXHAUSTED`, provider desativado/sem key pulado limpo, overrides de modelo preservando ordem, nenhum secret em log/exceção, segurança de streaming, reasoning/metadata nunca virando conteúdo, free_only respeitado, no máximo uma tentativa por candidato, cancelamento, classificação de timeout/conexão, bug interno nunca mascarado, telemetria de fallback, fluxo `AIService` compatível).
- `tests/test_ruflo_coordinator.py` (4 testes).

Todos 100% offline — nenhuma requisição real em nenhum teste automatizado.

## Smoke test real (opt-in, v1.4.0)

```bash
python scripts/test_ai_providers.py
```

Faz uma chamada mínima (`"Reply with OK."`, `max_tokens=8`) para cada modelo padrão de cada provider configurado — nunca roda no `pytest`, nunca insiste em cima de rate limit (uma tentativa por modelo), nunca imprime a API key nem o header `Authorization`. Saída: um `[OK]`/`[FAIL]`/`[SKIP]` por linha, com status HTTP/categoria de erro e latência — nunca um dump de resposta crua.

## O que esta versão **não** faz

- Não implementa Ruflo HIGH mode nem qualquer coordenação de swarm decidindo provider/modelo — isso é a próxima versão planejada, e mesmo lá o `ProviderRouter` continua como autoridade final (Ruflo coordena, nunca escolhe modelo).
- Não implementa streaming token-a-token real — ver a seção "Streaming" acima para por que a garantia de segurança já vale sem ele.
- Não tem discovery automático de catálogo em runtime — a lista de modelos por provider é curada por revisão de código + smoke test manual, não uma chamada a `GET /models` toda vez que o JARVIS abre.
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
