"""Autorização e execução de ações locais (v1.7).

--------------------------------------------------------------------------
A separação que este módulo garante
--------------------------------------------------------------------------
`app/intents.py` entende o que foi pedido. Este módulo decide se aquilo pode
acontecer e, só então, faz acontecer.

    entrada do usuário
        ↓
    IntentRouter.route()          entende  (não executa)
        ↓
    ActionRequest                 descrição do que foi entendido
        ↓
    ActionExecutor.execute()      autoriza  →  executa

A consequência que importa: nenhum caminho de interpretação ganha autoridade
de execução por ter "entendido" algo. Se um dia o modelo de IA propuser uma
ação, ela chega aqui como `ActionRequest` com `source="ai"` e passa
exatamente pelas mesmas checagens de risco e permissão que uma ação digitada
— um provider não é um usuário autenticado, e sugerir não é autorizar.

--------------------------------------------------------------------------
Níveis de risco
--------------------------------------------------------------------------
Reutiliza o `RiskLevel` que já existe em `app/models.py` desde a v0.5
(READ/ACTION/DANGEROUS) em vez de criar um enum paralelo LOW/MEDIUM/HIGH: são
a mesma escala com nomes diferentes, e duas escalas concorrentes é como se
produz uma ação classificada como segura num lugar e perigosa no outro.

    READ       nada muda no sistema — ler o clipboard, listar processos
    ACTION     muda algo reversível — abrir app, volume, screenshot
    DANGEROUS  muda algo difícil de desfazer — fechar programa com trabalho
               aberto, apagar, terminal

`DANGEROUS` **sempre** exige confirmação explícita, mesmo com permissão
concedida. Permissão é "o JARVIS pode fazer este tipo de coisa"; confirmação
é "faça ISTO, agora" — e a segunda não é substituível pela primeira.

--------------------------------------------------------------------------
Sem IA
--------------------------------------------------------------------------
Nada aqui depende de provider de IA. Abrir o Spotify não precisa de nuvem, e
continuar funcionando com todos os providers fora do ar é requisito, não
detalhe: ver `tests/test_command_bar_v17.py::OfflineTests`.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

from app.intents import Intent, RoutedIntent
from app.models import RiskLevel

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    """Ações locais que o JARVIS sabe executar nesta versão.

    Enumeração fechada de propósito: uma string livre viraria um caminho por
    onde uma ação não prevista (e não classificada quanto a risco) entraria
    no executor."""

    OPEN_APP = "open_app"
    CLOSE_APP = "close_app"
    OPEN_FOLDER = "open_folder"
    OPEN_URL = "open_url"
    VOLUME_SET = "volume_set"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    MUTE = "mute"
    UNMUTE = "unmute"
    SCREENSHOT = "screenshot"
    CLIPBOARD_READ = "clipboard_read"
    CLIPBOARD_SUMMARIZE = "clipboard_summarize"
    LIST_PROCESSES = "list_processes"
    SYSTEM_INFO = "system_info"
    CALCULATE = "calculate"
    OPEN_SETTINGS = "open_settings"


# Classificação de risco. Um tipo ausente deste mapa é tratado como
# DANGEROUS por `risk_of()` — falha para o lado seguro, e uma ação nova sem
# classificação explícita não passa despercebida.
_RISK: dict[ActionType, RiskLevel] = {
    ActionType.CLIPBOARD_READ: RiskLevel.READ,
    ActionType.CLIPBOARD_SUMMARIZE: RiskLevel.READ,
    ActionType.LIST_PROCESSES: RiskLevel.READ,
    ActionType.SYSTEM_INFO: RiskLevel.READ,
    ActionType.CALCULATE: RiskLevel.READ,
    ActionType.OPEN_APP: RiskLevel.ACTION,
    ActionType.OPEN_FOLDER: RiskLevel.ACTION,
    ActionType.OPEN_URL: RiskLevel.ACTION,
    ActionType.OPEN_SETTINGS: RiskLevel.ACTION,
    ActionType.VOLUME_SET: RiskLevel.ACTION,
    ActionType.VOLUME_UP: RiskLevel.ACTION,
    ActionType.VOLUME_DOWN: RiskLevel.ACTION,
    ActionType.MUTE: RiskLevel.ACTION,
    ActionType.UNMUTE: RiskLevel.ACTION,
    ActionType.SCREENSHOT: RiskLevel.ACTION,
    # Fechar um programa pode descartar trabalho não salvo. É a única ação
    # desta versão que o usuário não consegue desfazer clicando de novo.
    ActionType.CLOSE_APP: RiskLevel.DANGEROUS,
}

# Categoria de permissão por ação — o vocabulário que o Permissions Center da
# v1.9 vai apresentar. Declarado aqui, junto do risco, para uma ação nova não
# poder existir sem as duas coisas.
_PERMISSION: dict[ActionType, str] = {
    ActionType.OPEN_APP: "applications",
    ActionType.CLOSE_APP: "applications",
    ActionType.OPEN_FOLDER: "files",
    ActionType.OPEN_URL: "applications",
    ActionType.VOLUME_SET: "system_control",
    ActionType.VOLUME_UP: "system_control",
    ActionType.VOLUME_DOWN: "system_control",
    ActionType.MUTE: "system_control",
    ActionType.UNMUTE: "system_control",
    ActionType.SCREENSHOT: "screenshots",
    ActionType.CLIPBOARD_READ: "clipboard",
    ActionType.CLIPBOARD_SUMMARIZE: "clipboard",
    ActionType.LIST_PROCESSES: "system_control",
    ActionType.SYSTEM_INFO: "system_control",
    ActionType.CALCULATE: "",
    ActionType.OPEN_SETTINGS: "",
}


def risk_of(action_type: ActionType) -> RiskLevel:
    """Risco de uma ação. Desconhecida = `DANGEROUS`: falhar para o lado
    seguro é o que impede uma ação nova de entrar sem classificação."""
    return _RISK.get(action_type, RiskLevel.DANGEROUS)


def permission_of(action_type: ActionType) -> str:
    return _PERMISSION.get(action_type, "system_control")


@dataclass(frozen=True)
class ActionRequest:
    """Uma ação PROPOSTA — ainda não autorizada, ainda não executada.

    `source` registra de onde veio a proposta (`command_bar`, `chat`, `ai`,
    `voice`). Não é decorativo: é o que permite auditar depois que uma ação
    perigosa foi proposta pelo modelo e não pelo usuário."""

    action_type: ActionType
    parameters: dict[str, str] = field(default_factory=dict)
    source: str = "command_bar"

    @property
    def risk_level(self) -> RiskLevel:
        return risk_of(self.action_type)

    @property
    def permission(self) -> str:
        return permission_of(self.action_type)

    @property
    def requires_confirmation(self) -> bool:
        """Ação de risco alto sempre pergunta antes, mesmo com permissão
        concedida — permissão é uma categoria, confirmação é este ato."""
        return self.risk_level is RiskLevel.DANGEROUS

    def describe(self) -> str:
        """Frase curta para a UI de confirmação. Nunca inclui caminho
        completo nem conteúdo — só o suficiente para a pessoa reconhecer o
        que vai acontecer."""
        target = self.parameters.get("target") or self.parameters.get("url") or ""
        descriptions = {
            ActionType.OPEN_APP: f"Abrir {target}",
            ActionType.CLOSE_APP: f"Fechar {target}",
            ActionType.OPEN_FOLDER: f"Abrir a pasta {target}",
            ActionType.OPEN_URL: f"Abrir {target} no navegador",
            ActionType.VOLUME_SET: f"Ajustar o volume para {self.parameters.get('level', '')}%",
            ActionType.VOLUME_UP: "Aumentar o volume",
            ActionType.VOLUME_DOWN: "Diminuir o volume",
            ActionType.MUTE: "Silenciar o áudio",
            ActionType.UNMUTE: "Reativar o áudio",
            ActionType.SCREENSHOT: "Capturar a tela",
            ActionType.CLIPBOARD_READ: "Ler a área de transferência",
            ActionType.CLIPBOARD_SUMMARIZE: "Resumir a área de transferência",
            ActionType.LIST_PROCESSES: "Listar os processos em execução",
            ActionType.SYSTEM_INFO: "Mostrar informações do sistema",
            ActionType.CALCULATE: "Calcular",
            ActionType.OPEN_SETTINGS: "Abrir as configurações",
        }
        return descriptions.get(self.action_type, self.action_type.value)


@dataclass(frozen=True)
class ActionResult:
    """Resultado já apresentável. `detail` é texto para o usuário — nunca
    stack trace, nunca caminho de sistema desnecessário, nunca conteúdo
    sensível (ver `SystemControl.read_clipboard`)."""

    ok: bool
    detail: str = ""
    quick_actions: tuple[str, ...] = ()
    needs_confirmation: bool = False
    request: "ActionRequest | None" = None
    # v1.8 — referência OPACA ao arquivo a que as quick actions se aplicam.
    # Handle, nunca caminho: ver services/files/file_search.py::resolve_handle.
    file_handle: str = ""


# Intent -> ActionType. Só o que o executor sabe fazer entra aqui; CHAT,
# FILE_SEARCH e REMINDER não viram ação local nesta versão (a busca de
# arquivos é v1.8 e os lembretes são v1.9).
_INTENT_ACTIONS: dict[str, ActionType] = {value.value: value for value in ActionType}


def action_from_intent(routed: RoutedIntent, *, source: str = "command_bar") -> ActionRequest | None:
    """`RoutedIntent` -> `ActionRequest`, ou `None` quando a intenção não é
    uma ação local executável nesta versão."""
    if routed.intent is Intent.OPEN_APP:
        return ActionRequest(ActionType.OPEN_APP, dict(routed.parameters), source)
    if routed.intent is Intent.SETTINGS:
        return ActionRequest(ActionType.OPEN_SETTINGS, dict(routed.parameters), source)
    if routed.intent is Intent.TOOL and routed.parameters.get("tool") == "calculator":
        return ActionRequest(ActionType.CALCULATE, dict(routed.parameters), source)
    if routed.intent is Intent.SYSTEM_ACTION:
        name = routed.parameters.get("action", "")
        action_type = _INTENT_ACTIONS.get(name)
        if action_type is None:
            logger.warning("Ação de sistema desconhecida no roteamento: %r", name)
            return None
        parameters = {k: v for k, v in routed.parameters.items() if k != "action"}
        return ActionRequest(action_type, parameters, source)
    return None
