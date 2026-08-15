"""`CommandBarService` — o que acontece quando alguém digita na barra de
comando (v1.7).

    texto
      ↓
    IntentRouter          entende          (app/intents.py)
      ↓
    ActionRequest         descreve         (app/actions.py)
      ↓
    autorização           risco + confirmação
      ↓
    SystemControl         executa          (services/system/)
      ↓
    CommandBarResult      resposta + quick actions

Quando nada casa com um comando local, o resultado é `handled=False` e a
mensagem segue para a IA pelo caminho normal do chat. É isso que faz a barra
ser "universal" sem virar um segundo chat: ela intercepta o que sabe resolver
e não atrapalha o resto.

--------------------------------------------------------------------------
Confirmação
--------------------------------------------------------------------------
Ação de risco alto volta como `needs_confirmation=True` com a `ActionRequest`
anexada, e NÃO é executada. Quem confirma chama `confirm()` com aquela
mesma request. Não existe caminho em que um único envio de texto execute uma
ação perigosa — nem digitado, nem proposto pelo modelo.

--------------------------------------------------------------------------
Offline
--------------------------------------------------------------------------
Nada aqui importa provider de IA. Todo comando local funciona com a nuvem
inteira fora do ar; o único caminho que precisa de IA é o `CHAT`, que já
degrada sozinho (ver `ProviderRouterAIService`).
"""

import logging

from app.actions import ActionRequest, ActionResult, ActionType, action_from_intent
from app.intents import Intent, IntentRouter, RoutedIntent
from app.models import RiskLevel
from services.system.app_resolver import AppResolver
from services.system.system_control import SystemControl

logger = logging.getLogger(__name__)


class CommandBarService:
    def __init__(
        self,
        *,
        router: IntentRouter | None = None,
        apps: AppResolver | None = None,
        system: SystemControl | None = None,
        files=None,
        skills=None,
    ) -> None:
        """Todas as dependências são injetáveis para os testes usarem um
        Menu Iniciar falso e um controle de sistema fake — nenhum teste abre
        aplicativo, mexe no volume real ou tira print da máquina.

        `files` (v1.8) é o `FileSearchService`. Opcional: sem ele a busca de
        arquivos fica indisponível e o resto continua funcionando — é o que
        acontece antes de existir uma sessão com banco."""
        self._router = router or IntentRouter()
        self._apps = apps or AppResolver()
        self._system = system or SystemControl()
        self._files = files
        self._skills = skills

    # ------------------------------------------------------------------

    def submit(self, text: str, *, source: str = "command_bar") -> ActionResult:
        """Processa uma entrada. `ActionResult.ok=False` com `detail` vazio
        significa "não é comando local, mande para a IA"."""
        routed = self._router.route(text)

        if routed.is_chat:
            return ActionResult(ok=False, detail="")

        if routed.intent is Intent.FILE_SEARCH:
            return self._search_files(routed.parameters.get("query", ""))

        if routed.intent is Intent.REMINDER:
            # Lembretes são a v1.9. Mesmo raciocínio: não prometer o que não
            # existe. A IA responderia "claro, vou lembrar" sem agendar nada.
            return ActionResult(
                ok=False,
                detail="Lembretes ainda não estão disponíveis nesta versão.",
            )

        request = action_from_intent(routed, source=source)
        if request is None:
            return ActionResult(ok=False, detail="")

        if request.requires_confirmation:
            logger.info("Ação de risco alto aguardando confirmação: %s", request.action_type.value)
            return ActionResult(
                ok=False,
                detail=request.describe(),
                needs_confirmation=True,
                request=request,
            )

        return self.execute(request, routed=routed)

    def _search_files(self, query: str) -> ActionResult:
        """Busca de arquivos (v1.8). 100% local: nenhum provider é chamado,
        nem para ranquear."""
        if self._files is None:
            return ActionResult(
                ok=False, detail="A busca de arquivos ainda não está pronta. Tente de novo em instantes."
            )
        results = self._files.search(query)
        if not results:
            return ActionResult(ok=True, detail=f"Nenhum arquivo encontrado para \"{query}\".")

        lines = [f"{len(results)} arquivo(s) encontrado(s):"]
        for item in results:
            line = f"  {item.name} — {item.parent_label} · {_friendly(item)}"
            if item.snippet:
                line += f"\n    …{item.snippet}…"
            lines.append(line)
        return ActionResult(
            ok=True,
            detail="\n".join(lines),
            # Quick actions do PRIMEIRO resultado: é o que a pessoa quer
            # fazer em seguida na esmagadora maioria das vezes, e encher a
            # tela com quatro botões por arquivo seria ruído.
            quick_actions=("open_file", "show_in_folder", "copy_path", "summarize_file"),
            file_handle=results[0].handle,
        )

    def confirm(self, request: ActionRequest) -> ActionResult:
        """Executa uma ação que estava aguardando confirmação.

        Recebe a `ActionRequest` de volta (e não um id) de propósito: o que é
        executado é exatamente o que foi mostrado na tela de confirmação, sem
        um estado intermediário que pudesse ser trocado no meio."""
        logger.info("Ação de risco alto confirmada: %s", request.action_type.value)
        return self.execute(request, confirmed=True)

    # ------------------------------------------------------------------

    def execute(
        self, request: ActionRequest, *, routed: RoutedIntent | None = None, confirmed: bool = False
    ) -> ActionResult:
        """Executa uma ação já autorizada.

        A checagem de confirmação é repetida aqui e não só em `submit()`: este
        método é público, e uma chamada direta com uma ação perigosa não pode
        escapar da regra só por ter pulado a porta da frente."""
        if request.risk_level is RiskLevel.DANGEROUS and not confirmed:
            return ActionResult(
                ok=False, detail=request.describe(), needs_confirmation=True, request=request
            )

        handler = getattr(self, f"_do_{request.action_type.value}", None)
        if handler is None:
            logger.warning("Sem handler para a ação %s", request.action_type.value)
            return ActionResult(ok=False, detail="Não sei executar essa ação.")
        return handler(request)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _do_open_app(self, request: ActionRequest) -> ActionResult:
        target = request.parameters.get("target", "")
        app = self._apps.resolve(target)
        if app is None:
            return ActionResult(ok=False, detail=f"Não encontrei nenhum aplicativo chamado \"{target}\".")
        ok, detail = self._system.open_path(app.path)
        if not ok:
            return ActionResult(ok=False, detail=detail)
        return ActionResult(ok=True, detail=f"Abrindo {app.display_name}", quick_actions=("close_app",))

    def _do_close_app(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.close_app(request.parameters.get("target", ""))
        return ActionResult(ok=ok, detail=detail)

    def _do_open_folder(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.open_path(request.parameters.get("target", ""))
        return ActionResult(ok=ok, detail=detail)

    def _do_open_url(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.open_url(request.parameters.get("url", ""))
        return ActionResult(ok=ok, detail=detail)

    def _do_volume_set(self, request: ActionRequest) -> ActionResult:
        try:
            level = int(request.parameters.get("level", "0"))
        except ValueError:
            return ActionResult(ok=False, detail="Nível de volume inválido.")
        ok, detail = self._system.volume_set(level)
        return ActionResult(ok=ok, detail=detail)

    def _do_volume_up(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.volume_up()
        return ActionResult(ok=ok, detail=detail)

    def _do_volume_down(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.volume_down()
        return ActionResult(ok=ok, detail=detail)

    def _do_mute(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.mute()
        return ActionResult(ok=ok, detail=detail)

    def _do_unmute(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.unmute()
        return ActionResult(ok=ok, detail=detail)

    def _do_screenshot(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.screenshot()
        if not ok:
            return ActionResult(ok=False, detail=detail)
        return ActionResult(
            ok=True,
            detail="Captura salva",
            # O caminho vira quick action e não texto: abrir a pasta é o que
            # a pessoa quer fazer em seguida, e imprimir o caminho completo
            # na conversa é ruído.
            quick_actions=("open_screenshot", "show_in_folder"),
        )

    def _do_clipboard_read(self, request: ActionRequest) -> ActionResult:
        ok, detail = self._system.read_clipboard()
        return ActionResult(ok=ok, detail=detail)

    def _do_clipboard_summarize(self, request: ActionRequest) -> ActionResult:
        """Ler é local; RESUMIR precisa da IA.

        A leitura acontece aqui e o texto volta para o chamador mandar ao
        chat — o clipboard nunca é enviado a um provider por este módulo, e
        nunca sem o usuário ter pedido explicitamente um resumo."""
        ok, content = self._system.read_clipboard()
        if not ok:
            return ActionResult(ok=False, detail=content)
        return ActionResult(ok=True, detail=content, quick_actions=("summarize_with_ai",))

    def _do_list_processes(self, request: ActionRequest) -> ActionResult:
        ok, processes = self._system.list_processes()
        if not ok:
            return ActionResult(ok=False, detail="Não foi possível listar os processos.")
        if not processes:
            return ActionResult(ok=True, detail="Nenhum processo encontrado.")
        lines = [f"{item.name} — {item.memory_mb:.0f} MB" for item in processes]
        return ActionResult(ok=True, detail="\n".join(lines))

    def _do_system_info(self, request: ActionRequest) -> ActionResult:
        ok, info = self._system.system_info()
        if not ok:
            return ActionResult(ok=False, detail="Não foi possível ler as informações do sistema.")
        lines = [f"{key}: {value}" for key, value in info.items()]
        return ActionResult(ok=True, detail="\n".join(lines))

    def _do_open_settings(self, request: ActionRequest) -> ActionResult:
        # Quem abre a tela é o frontend; o serviço só sinaliza a intenção.
        return ActionResult(ok=True, detail="Abrindo as configurações", quick_actions=("open_settings",))

    def _do_calculate(self, request: ActionRequest) -> ActionResult:
        parameters = request.parameters
        if "percent" in parameters and "value" in parameters:
            try:
                percent = float(parameters["percent"].replace(",", "."))
                value = float(parameters["value"].replace(",", "."))
            except ValueError:
                return ActionResult(ok=False, detail="Não consegui interpretar esses números.")
            result = percent / 100 * value
            return ActionResult(ok=True, detail=self._format_number(result))

        expression = parameters.get("expression", "")
        value = _safe_eval(expression)
        if value is None:
            return ActionResult(ok=False, detail="Não consegui calcular isso.")
        return ActionResult(ok=True, detail=self._format_number(value))

    @staticmethod
    def _format_number(value: float) -> str:
        """Inteiro sai sem casa decimal: "500" e não "500.0"."""
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.4f}".rstrip("0").rstrip(".")


# ----------------------------------------------------------------------


def _friendly(item) -> str:
    """Data relativa curta para a linha do resultado."""
    return item.to_view()["modified"]


def _safe_eval(expression: str) -> float | None:
    """Avalia uma expressão aritmética SEM `eval`.

    `eval` num texto vindo do usuário é execução de código arbitrário — mesmo
    com `__builtins__` vazio, existem caminhos conhecidos de fuga. Aqui a
    expressão é convertida em árvore por `ast.parse` e percorrida nó a nó,
    aceitando apenas número e as quatro operações (mais potência e módulo).
    Qualquer outro nó — nome, chamada, atributo, subscrito — faz a avaliação
    devolver `None`.

    Isso NÃO é uma versão restrita de `eval`: é um interpretador próprio de
    aritmética, e nenhum objeto Python é alcançável a partir dele."""
    import ast
    import operator

    binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    def evaluate(node) -> float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("constante não numérica")
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            # Teto no expoente: `9**9**9` travaria o processo calculando um
            # número com bilhões de dígitos.
            if isinstance(node.op, ast.Pow):
                exponent = evaluate(node.right)
                if abs(exponent) > 64:
                    raise ValueError("expoente grande demais")
                return binary[type(node.op)](evaluate(node.left), exponent)
            return binary[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](evaluate(node.operand))
        raise ValueError(f"nó não permitido: {type(node).__name__}")

    cleaned = (expression or "").strip().replace("^", "**")
    if not cleaned or len(cleaned) > 200:
        return None
    try:
        tree = ast.parse(cleaned, mode="eval")
        return evaluate(tree.body)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
