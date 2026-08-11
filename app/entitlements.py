"""Entitlements: o que cada plano (`app.models.Plan`) habilita.

Fundação apenas (v0.9) — quase tudo é igual entre FREE e PRO hoje, de
propósito (ver `docs/architecture.md`, seção Planos). O ponto é ter UM lugar
que resolve "o que este usuário pode fazer" — nada no resto do projeto deve
comparar `user.plan == Plan.PRO` diretamente; sempre chamar
`entitlements_for(user.plan)`.

Sem billing: nenhuma cobrança, nenhuma integração de pagamento. Trocar de
plano nesta versão é só trocar o valor no banco (não há UI para isso ainda,
nem precisa — ver item "Planejado" na documentação).
"""

from dataclasses import dataclass

from app.models import Plan


@dataclass(frozen=True)
class Entitlements:
    plan: Plan
    # Limite de mensagens em RAM por conversa (app/conversation.py). PRO tem
    # um teto maior hoje só para a arquitetura já provar que é configurável
    # por plano — não é um limite real sentido por ninguém no FREE ainda.
    max_conversation_messages: int
    # Preparação para capabilities futuras (tools avançadas, Ruflo) — nenhuma
    # delas existe ainda; os campos só documentam a direção.
    advanced_tools: bool
    multi_agent: bool


_FREE = Entitlements(
    plan=Plan.FREE,
    max_conversation_messages=200,
    advanced_tools=False,
    multi_agent=False,
)

_PRO = Entitlements(
    plan=Plan.PRO,
    max_conversation_messages=400,
    advanced_tools=False,  # PLANEJADO — nenhuma tool real existe ainda, nem para PRO
    multi_agent=False,  # PLANEJADO — Ruflo não instalado
)


def entitlements_for(plan: Plan) -> Entitlements:
    return _PRO if plan is Plan.PRO else _FREE
