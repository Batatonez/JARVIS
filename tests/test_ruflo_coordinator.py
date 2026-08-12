"""Testes do `LocalRufloCoordinator` — só leitura local (JSON temporário),
nunca o `.claude-flow/` real do projeto, nunca subprocess, nunca rede."""

import json
import tempfile
import unittest
from pathlib import Path

from services.providers.ruflo_coordinator import LocalRufloCoordinator, NotYetImplementedError


class LocalRufloCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime_dir = Path(self._tmp.name)

    async def test_get_state_returns_empty_when_no_store_file(self) -> None:
        coordinator = LocalRufloCoordinator(runtime_dir=self.runtime_dir)
        self.assertEqual(await coordinator.get_state(), [])

    async def test_get_state_reads_agents_from_store_json(self) -> None:
        agents_dir = self.runtime_dir / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "store.json").write_text(
            json.dumps(
                {
                    "agents": {
                        "jarvis-coder": {
                            "agentType": "coder",
                            "status": "idle",
                            "modelRoutedBy": "explicit",
                        }
                    },
                    "version": "3.0.0",
                }
            ),
            encoding="utf-8",
        )

        coordinator = LocalRufloCoordinator(runtime_dir=self.runtime_dir)
        state = await coordinator.get_state()

        self.assertEqual(len(state), 1)
        self.assertEqual(state[0].agent_id, "jarvis-coder")
        self.assertEqual(state[0].agent_type, "coder")
        self.assertEqual(state[0].status, "idle")
        self.assertEqual(state[0].model_routed_by, "explicit")

    async def test_get_state_tolerates_corrupted_store_file(self) -> None:
        agents_dir = self.runtime_dir / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "store.json").write_text("{ not valid json", encoding="utf-8")

        coordinator = LocalRufloCoordinator(runtime_dir=self.runtime_dir)
        self.assertEqual(await coordinator.get_state(), [])

    async def test_creation_distribution_methods_are_not_yet_wired(self) -> None:
        coordinator = LocalRufloCoordinator(runtime_dir=self.runtime_dir)
        with self.assertRaises(NotYetImplementedError):
            await coordinator.create_swarm(topology="hierarchical-mesh", max_agents=8)
        with self.assertRaises(NotYetImplementedError):
            await coordinator.register_role(agent_type="coder", role="implementer")
        with self.assertRaises(NotYetImplementedError):
            await coordinator.distribute_task(agent_id="jarvis-coder", task="oi")


if __name__ == "__main__":
    unittest.main()
