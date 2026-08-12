"""Fronteira entre o JARVIS e o Ruflo (ver `docs/ruflo-integration.md`).

`RufloCoordinator` é só uma interface de coordenação — swarm, roles, tarefas,
estado. **Nunca** decide provider/modelo: isso é autoridade exclusiva do
`ProviderRouter` (`services/providers/router.py`). O motivo é concreto, não
teórico — `agent_execute` do Ruflo tem um bug de model routing confirmado
(registra `provider`/`openrouterModel` corretamente no spawn, mas o executor
ignora os dois campos e sempre resolve pra um ID nativo Anthropic; ver
`docs/ruflo-integration.md`), então nenhuma decisão de custo pode depender
dele.

Nesta etapa, `create_swarm`/`register_role`/`distribute_task` ficam como
contrato (não implementados de verdade — ver item 22 do pedido: "não
integrar agent_execute no caminho principal ainda"). `get_state()` é real:
lê o estado local que o daemon do Ruflo já escreve (`.claude-flow/agents/`,
`.claude-flow/swarm/`) — leitura pura, sem rede, sem subprocess, sem tocar
model routing.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwarmAgentInfo:
    agent_id: str
    agent_type: str
    status: str
    model_routed_by: str | None = None


class RufloCoordinator(ABC):
    @abstractmethod
    async def create_swarm(self, *, topology: str, max_agents: int) -> str:
        """Cria um swarm Ruflo. PLANEJADO — ver docstring do módulo."""

    @abstractmethod
    async def register_role(self, *, agent_type: str, role: str) -> None:
        """Registra um papel/role para um tipo de agente. PLANEJADO."""

    @abstractmethod
    async def distribute_task(self, *, agent_id: str, task: str) -> None:
        """Distribui uma tarefa a um agente já registrado. PLANEJADO — não
        chama `agent_execute` (ver docstring do módulo)."""

    @abstractmethod
    async def get_state(self) -> list[SwarmAgentInfo]:
        """Lista os agentes conhecidos pelo daemon do Ruflo agora."""


class NotYetImplementedError(NotImplementedError):
    """Levantada pelos métodos ainda não conectados — mensagem sempre
    explica O QUE falta e POR QUE, nunca só 'not implemented'."""


class LocalRufloCoordinator(RufloCoordinator):
    """Implementação que só lê o estado local já escrito pelo daemon do
    Ruflo (`.claude-flow/`) — nunca inicia um processo, nunca chama MCP,
    nunca decide modelo. `runtime_dir` é injetável para testes (nunca o
    `.claude-flow/` real do projeto em `tests/`)."""

    def __init__(self, *, runtime_dir: Path) -> None:
        self._runtime_dir = Path(runtime_dir)

    async def create_swarm(self, *, topology: str, max_agents: int) -> str:
        raise NotYetImplementedError(
            "create_swarm() ainda não está conectado ao Ruflo real nesta etapa "
            "(item 22 do pedido: agent_execute fora do caminho principal até o "
            "bug de model routing ser corrigido/contornado de forma segura)."
        )

    async def register_role(self, *, agent_type: str, role: str) -> None:
        raise NotYetImplementedError(
            "register_role() ainda não está conectado ao Ruflo real nesta etapa."
        )

    async def distribute_task(self, *, agent_id: str, task: str) -> None:
        raise NotYetImplementedError(
            "distribute_task() ainda não está conectado ao Ruflo real nesta etapa "
            "— nenhuma chamada de IA do JARVIS passa por agent_execute (ver "
            "docs/ruflo-integration.md)."
        )

    async def get_state(self) -> list[SwarmAgentInfo]:
        store_path = self._runtime_dir / "agents" / "store.json"
        if not store_path.is_file():
            return []
        try:
            data = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Não foi possível ler o estado local do Ruflo em %s.", store_path)
            return []

        agents = data.get("agents", {})
        return [
            SwarmAgentInfo(
                agent_id=agent_id,
                agent_type=record.get("agentType", "unknown"),
                status=record.get("status", "unknown"),
                model_routed_by=record.get("modelRoutedBy"),
            )
            for agent_id, record in agents.items()
        ]
