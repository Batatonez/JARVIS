import unittest

from app.models import PermissionStatus, RiskLevel
from app.permissions import PermissionRequestNotFoundError, PermissionService
from services.event_bus import EventBus


class PermissionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.service = PermissionService(self.event_bus)

    def test_request_creates_pending_permission(self) -> None:
        req = self.service.request("restart_server", "Reiniciar o servidor", RiskLevel.DANGEROUS)

        self.assertEqual(req.status, PermissionStatus.PENDING)
        self.assertEqual(req.risk_level, RiskLevel.DANGEROUS)
        self.assertIn(req, self.service.list_pending())

    def test_request_emits_permission_requested_event(self) -> None:
        received = []
        self.event_bus.subscribe("permission.requested", lambda **payload: received.append(payload))

        req = self.service.request("read_file", "Ler um arquivo", RiskLevel.READ)

        self.assertEqual(received, [{"request_id": req.id, "action": "read_file", "risk_level": "read"}])

    def test_approve_resolves_and_removes_from_pending(self) -> None:
        req = self.service.request("delete_file", "Apagar um arquivo", RiskLevel.DANGEROUS)

        resolved = self.service.approve(req.id)

        self.assertEqual(resolved.status, PermissionStatus.APPROVED)
        self.assertNotIn(resolved, self.service.list_pending())

    def test_deny_resolves_and_removes_from_pending(self) -> None:
        req = self.service.request("delete_file", "Apagar um arquivo", RiskLevel.DANGEROUS)

        resolved = self.service.deny(req.id)

        self.assertEqual(resolved.status, PermissionStatus.DENIED)
        self.assertNotIn(resolved, self.service.list_pending())

    def test_resolve_emits_permission_resolved_event(self) -> None:
        received = []
        self.event_bus.subscribe("permission.resolved", lambda **payload: received.append(payload))
        req = self.service.request("delete_file", "Apagar um arquivo", RiskLevel.DANGEROUS)

        self.service.approve(req.id)

        self.assertEqual(received, [{"request_id": req.id, "status": "approved"}])

    def test_resolving_unknown_request_raises(self) -> None:
        with self.assertRaises(PermissionRequestNotFoundError):
            self.service.approve("id-que-nao-existe")


if __name__ == "__main__":
    unittest.main()
