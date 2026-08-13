"""Comando `jarvis` — abre o HUD sem precisar digitar `python -m frontend`.

    jarvis
    jarvis wake
    jarvis wake up
    jarvis start

Todos fazem exatamente a mesma coisa: chamam `frontend.launcher.run()`, o
mesmo entrypoint que `python -m frontend` sempre usou. Este módulo não
duplica nada da inicialização — `.env`, auto-login, ProviderRouter, voz e
encerramento continuam sendo responsabilidade do launcher.

`python -m frontend` continua funcionando inalterado; este é um atalho a
mais, não um substituto.

A separação entre `resolve_command()` (decisão pura) e `main()` (efeito) é
o que permite testar todo o parser sem nunca abrir uma janela.
"""

import sys
from enum import Enum

HELP_TEXT = """JARVIS

Usage:
  jarvis
  jarvis wake
  jarvis wake up
  jarvis start"""

# Formas aceitas para "inicie o JARVIS". A comparação é feita sobre os
# argumentos unidos por espaço e normalizados (minúsculas, sem espaço
# redundante), então `jarvis   WAKE   UP` também funciona.
_START_COMMANDS = frozenset({"", "wake", "wake up", "start"})
_HELP_FLAGS = frozenset({"-h", "--help", "help"})


class Command(Enum):
    START = "start"  # abrir o HUD
    HELP = "help"  # ajuda pedida explicitamente (saída 0)
    UNKNOWN = "unknown"  # argumento não reconhecido (ajuda + saída 2)


def resolve_command(args: list[str]) -> Command:
    """Decide o que fazer com os argumentos. Função pura — nenhum efeito
    colateral, nada de Qt, nada de I/O."""
    normalized = " ".join(" ".join(args).lower().split())
    if normalized in _START_COMMANDS:
        return Command.START
    if normalized in _HELP_FLAGS:
        return Command.HELP
    return Command.UNKNOWN


def main(argv: list[str] | None = None) -> int:
    """Entrypoint do console script (ver `[project.scripts]` no
    pyproject.toml). Devolve o código de saída do processo."""
    args = sys.argv[1:] if argv is None else list(argv)
    command = resolve_command(args)

    if command is Command.HELP:
        print(HELP_TEXT)
        return 0
    if command is Command.UNKNOWN:
        # 2 é a convenção para erro de uso (o mesmo que argparse usa),
        # distinguindo "você pediu ajuda" de "você errou o comando".
        print(HELP_TEXT, file=sys.stderr)
        return 2

    # Import tardio de propósito: carregar o Qt custa caro, e `jarvis --help`
    # não precisa pagar por isso. Também mantém este módulo importável (e
    # testável) sem tocar na stack gráfica.
    from frontend.launcher import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
