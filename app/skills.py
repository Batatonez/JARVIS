"""Skills — a camada extensível de ferramentas do JARVIS (v1.8).

--------------------------------------------------------------------------
O problema que resolve
--------------------------------------------------------------------------
Cada capacidade nova (calculadora, sistema, arquivos, e depois lembretes,
calendário, web) adicionava mais um ramo em `CommandBarService`. Isso
funciona com três e vira um `switch` gigante com dez — onde cada ramo
reimplementa à sua maneira a checagem de permissão e de risco, e um deles
inevitavelmente esquece.

Uma `Skill` declara o que faz, o que precisa e qual o risco. O
`SkillRegistry` coordena. Nenhuma skill reimplementa serviço: `FilesSkill`
usa o `FileSearchService`, `SystemSkill` usa o `SystemControl`,
`CalculatorSkill` usa a calculadora que já existe. A camada é de
COORDENAÇÃO, não uma segunda cópia dos serviços.

--------------------------------------------------------------------------
Fronteira com o modelo de IA
--------------------------------------------------------------------------
Esta arquitetura existe para que, quando o modelo puder pedir uma ferramenta,
o pedido passe pelo mesmo caminho de tudo o mais:

    modelo propõe chamada estruturada
        ↓
    validação de schema        (nome existe? argumentos batem?)
        ↓
    SkillRegistry              (skill existe? está disponível?)
        ↓
    permissão + risco          (a mesma tabela de app/actions.py)
        ↓
    confirmação se necessário  (risco alto sempre pergunta)
        ↓
    execução

Propor não é autorizar. `source="ai"` não abrevia nenhuma etapa — existe
teste para isso.

**Não existe, e não vai existir nesta versão, uma skill que execute comando
de shell arbitrário.** Uma `shell(command: str)` transformaria toda a
validação acima em teatro: bastaria o modelo (ou um documento que ele leu)
propor uma string.

--------------------------------------------------------------------------
Handles em vez de caminho
--------------------------------------------------------------------------
`Files.open` recebe `file_2`, não `C:\\Users\\...`. O handle só resolve para
um resultado da busca que acabou de acontecer, então um caminho inventado —
por engano ou por instrução escondida num documento — simplesmente não
resolve. Ver `FileSearchService.resolve_handle`.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import RiskLevel

logger = logging.getLogger(__name__)


class SkillError(Exception):
    """Falha já explicada, apresentável ao usuário."""


@dataclass(frozen=True)
class SkillParameter:
    name: str
    type: str  # "string" | "integer" | "boolean"
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class SkillAction:
    """Uma operação de uma skill, com schema declarado.

    O schema não é documentação: é o que `validate_arguments` usa para
    recusar uma chamada malformada ANTES de qualquer execução — inclusive
    uma vinda do modelo."""

    name: str
    description: str
    parameters: tuple[SkillParameter, ...] = ()
    risk_level: RiskLevel = RiskLevel.READ
    permission: str = ""
    handler: Callable[..., Any] | None = None

    @property
    def requires_confirmation(self) -> bool:
        return self.risk_level is RiskLevel.DANGEROUS

    def schema(self) -> dict:
        """Forma serializável — o que seria oferecido a um modelo como
        definição de ferramenta."""
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk_level.value,
            "permission": self.permission,
            "parameters": {
                parameter.name: {
                    "type": parameter.type,
                    "required": parameter.required,
                    "description": parameter.description,
                }
                for parameter in self.parameters
            },
        }

    def validate_arguments(self, arguments: dict) -> dict:
        """Valida e converte os argumentos. Levanta `SkillError` no primeiro
        problema.

        Argumento DESCONHECIDO é erro, não algo a ignorar: uma chamada com um
        parâmetro que a skill não declara significa que quem chamou entendeu
        errado o contrato, e executar assim mesmo é como se passa um valor
        para o lugar errado."""
        declared = {parameter.name: parameter for parameter in self.parameters}

        unknown = set(arguments) - set(declared)
        if unknown:
            raise SkillError(f"Parâmetro desconhecido em {self.name}: {', '.join(sorted(unknown))}")

        cleaned: dict[str, Any] = {}
        for name, parameter in declared.items():
            if name not in arguments:
                if parameter.required:
                    raise SkillError(f"Parâmetro obrigatório ausente em {self.name}: {name}")
                continue
            cleaned[name] = self._coerce(parameter, arguments[name])
        return cleaned

    @staticmethod
    def _coerce(parameter: SkillParameter, value: Any) -> Any:
        if parameter.type == "integer":
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise SkillError(f"{parameter.name} precisa ser um número inteiro.") from exc
        if parameter.type == "boolean":
            return bool(value)
        if value is None:
            raise SkillError(f"{parameter.name} não pode ser vazio.")
        return str(value)


@dataclass
class Skill:
    """Um agrupamento de ações relacionadas.

    `available` é uma função e não um booleano: a disponibilidade muda em
    runtime (o índice de arquivos pode não ter sido construído, o controle de
    sistema não existe fora do Windows), e congelar isso na criação daria uma
    resposta errada minutos depois."""

    id: str
    name: str
    description: str
    actions: dict[str, SkillAction] = field(default_factory=dict)
    available: Callable[[], bool] = lambda: True

    def action(self, name: str) -> SkillAction | None:
        return self.actions.get(name)

    def schema(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "available": self.available(),
            "actions": [action.schema() for action in self.actions.values()],
        }


@dataclass(frozen=True)
class SkillResult:
    ok: bool
    detail: str = ""
    data: dict = field(default_factory=dict)
    needs_confirmation: bool = False
    skill_id: str = ""
    action_name: str = ""


class SkillRegistry:
    """Onde as skills vivem. Coordena; não implementa nenhuma delas."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Registra uma skill. ID duplicado é ERRO, não substituição
        silenciosa: duas skills com o mesmo id significa que uma delas nunca
        seria chamada, e descobrir isso em produção é caro."""
        if skill.id in self._skills:
            raise SkillError(f"Já existe uma skill registrada com o id {skill.id!r}.")
        self._skills[skill.id] = skill
        logger.info("Skill registrada: %s (%s ações)", skill.id, len(skill.actions))

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def schemas(self) -> list[dict]:
        """Definições de todas as skills DISPONÍVEIS — o que seria oferecido
        a um modelo. Skill indisponível fica de fora: oferecer uma ferramenta
        que vai falhar é pior que não oferecer."""
        return [skill.schema() for skill in self._skills.values() if skill.available()]

    def execute(
        self,
        skill_id: str,
        action_name: str,
        arguments: dict | None = None,
        *,
        source: str = "user",
        confirmed: bool = False,
    ) -> SkillResult:
        """Executa uma ação de skill, com todas as checagens na ordem.

        `source` é registrado mas NUNCA relaxa nada — em particular,
        `source="ai"` passa exatamente pelas mesmas etapas que `"user"`."""
        skill = self._skills.get(skill_id)
        if skill is None:
            return SkillResult(ok=False, detail=f"Não conheço a habilidade {skill_id!r}.")
        if not skill.available():
            return SkillResult(ok=False, detail=f"{skill.name} não está disponível agora.")

        action = skill.action(action_name)
        if action is None:
            return SkillResult(ok=False, detail=f"{skill.name} não tem a ação {action_name!r}.")

        try:
            cleaned = action.validate_arguments(arguments or {})
        except SkillError as exc:
            return SkillResult(ok=False, detail=str(exc), skill_id=skill_id, action_name=action_name)

        if action.requires_confirmation and not confirmed:
            logger.info("Ação de skill de risco alto aguardando confirmação: %s.%s", skill_id, action_name)
            return SkillResult(
                ok=False,
                detail=action.description,
                needs_confirmation=True,
                skill_id=skill_id,
                action_name=action_name,
                data=dict(cleaned),
            )

        if action.handler is None:
            return SkillResult(ok=False, detail="Essa ação ainda não faz nada.")

        try:
            result = action.handler(**cleaned)
        except SkillError as exc:
            return SkillResult(ok=False, detail=str(exc), skill_id=skill_id, action_name=action_name)
        except Exception:
            # O traceback vai para o log; o usuário recebe uma frase. Uma
            # skill quebrada não pode derrubar a Command Bar nem vazar
            # caminho interno na tela.
            logger.exception("Falha ao executar %s.%s", skill_id, action_name)
            return SkillResult(
                ok=False, detail="Não foi possível concluir essa ação.",
                skill_id=skill_id, action_name=action_name,
            )

        if isinstance(result, SkillResult):
            return result
        return SkillResult(ok=True, detail=str(result or ""), skill_id=skill_id, action_name=action_name)
