"""PermissionService: fundação mínima para permissões futuras (READ/ACTION/DANGEROUS).

Nesta etapa isto NÃO está conectado a nenhuma ferramenta real — não existe
execução de ações, Bash, PowerShell ou controle do sistema. A única
finalidade é garantir que o futuro HUD consiga mostrar algo como:

    JARVIS deseja realizar uma ação.
    [Permitir] [Negar]

sem exigirmos um redesenho do backend quando ferramentas reais existirem.
Funciona inteiramente em memória; nada é persistido.
"""

import logging

from app.models import PermissionRequest, PermissionStatus, RiskLevel
from services.event_bus import EventBus

logger = logging.getLogger(__name__)


class PermissionRequestNotFoundError(Exception):
    """Levantada ao resolver um pedido de permissão que não existe."""


class PermissionService:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._requests: dict[str, PermissionRequest] = {}

    def request(self, action: str, description: str, risk_level: RiskLevel) -> PermissionRequest:
        req = PermissionRequest(action=action, description=description, risk_level=risk_level)
        self._requests[req.id] = req
        logger.info("Permissão solicitada: %s (%s)", action, risk_level.value)
        self._event_bus.emit(
            "permission.requested", request_id=req.id, action=action, risk_level=risk_level.value
        )
        return req

    def list_pending(self) -> list[PermissionRequest]:
        return [r for r in self._requests.values() if r.status is PermissionStatus.PENDING]

    def approve(self, request_id: str) -> PermissionRequest:
        return self._resolve(request_id, PermissionStatus.APPROVED)

    def deny(self, request_id: str) -> PermissionRequest:
        return self._resolve(request_id, PermissionStatus.DENIED)

    def _resolve(self, request_id: str, status: PermissionStatus) -> PermissionRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise PermissionRequestNotFoundError(f"Pedido de permissão não encontrado: {request_id}")
        req.status = status
        logger.info("Permissão %s: %s", status.value, req.action)
        self._event_bus.emit("permission.resolved", request_id=req.id, status=status.value)
        return req
