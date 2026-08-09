# API da Application Layer (`JarvisApplication`)

Este é o contrato que qualquer frontend (terminal hoje; HUD, voz, etc. no
futuro) deve usar para falar com o JARVIS — nunca `JarvisCore`, `Orchestrator`,
`MemoryService` ou `services/claude_agent_provider.py` diretamente.

```python
from app.core import JarvisCore
from app.application import JarvisApplication

core = JarvisCore()
app = JarvisApplication(core)
```

## Como iniciar / parar

```python
await app.start()   # idempotente
await app.stop()    # idempotente; cancela requisição pendente antes de parar
```

## Como enviar uma mensagem

```python
response = await app.send_message("estou trabalhando no BatataMC")
# response: AssistantResponse(status=SUCCESS|CANCELLED|ERROR, message_id, content, error)
```

- Retorna `None` para entrada vazia (nada a fazer).
- Nunca levanta exceção para falhas esperadas — `status`/`error.code` já vêm
  estruturados (`AI_UNAVAILABLE`, `JARVIS_BUSY`, `INTERNAL_ERROR`), sem exigir
  que o frontend analise texto humano.
- Política de concorrência: **uma requisição ativa por conversa**. Uma segunda
  chamada enquanto a primeira está em andamento é rejeitada com
  `status=ERROR, error.code=JARVIS_BUSY` — não é enfileirada.

## Como observar eventos em tempo real

```python
queue = app.subscribe()
event = await queue.get()      # event: AppEvent(type, timestamp, payload)
app.unsubscribe(queue)

# ou, equivalente:
async for event in app.events():
    ...
```

Eventos existentes: `jarvis.started`, `jarvis.stopping`, `jarvis.stopped`,
`state.changed`, `ai.connected`, `ai.disconnected`, `conversation.started`,
`conversation.cleared`, `message.received`, `response.started`,
`response.completed`, `response.failed`, `permission.requested`,
`permission.resolved`. O `payload` contém só tipos simples (str/bool/número) —
nunca um objeto interno do Core ou do Claude Agent SDK.

## Como obter o estado atual

```python
status = app.get_status()
# StatusSnapshot(core_version, state, running, busy, memory_available,
#                 ai_configured, ai_backend, ai_session_active, active_conversation)
```

Mesma fonte usada pelo comando `/status` do terminal (`app/status.py`).

## Como obter o histórico da conversa

```python
messages = app.get_messages()
# list[Message(id, role, content, timestamp)] — só a sessão atual, em RAM.
```

Isto é diferente de `memory/` (memória persistente sobre o usuário — ver
`CLAUDE.md`): o histórico de conversa desaparece ao encerrar o JARVIS.

## Como cancelar a resposta atual

```python
cancelled = await app.cancel_current_request()  # True se havia algo a cancelar
```

Usa `asyncio.Task.cancel()` — nunca mata processos. Depois de cancelar, o
estado volta para `IDLE` e `busy` volta para `False`.

## Como iniciar uma nova conversa

```python
await app.new_conversation()
```

Limpa o histórico runtime e, se houver IA configurada, reinicia a sessão do
Agent SDK (contexto limpo). **Não** apaga `memory/profile.md` nem
`memory/preferences.md`. No terminal, isso é o comando `/new` (alias `/reset`).

## Permissões (fundação, não conectada a ferramentas reais)

```python
from app.models import RiskLevel

req = app.permissions.request("restart_server", "Reiniciar o servidor", RiskLevel.DANGEROUS)
app.permissions.list_pending()
app.permissions.approve(req.id)   # ou .deny(req.id)
```

Existe só para o futuro HUD conseguir mostrar "JARVIS deseja realizar uma
ação — [Permitir] [Negar]" sem redesenho do backend. Nenhuma ferramenta real
usa isso ainda.
