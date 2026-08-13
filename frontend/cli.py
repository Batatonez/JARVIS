"""Comando `jarvis` — abre o HUD sem precisar digitar `python -m frontend`.

    jarvis
    jarvis wake
    jarvis wake up
    jarvis start
    jarvis delete all users

Os quatro primeiros fazem exatamente a mesma coisa: chamam
`frontend.launcher.run()`, o mesmo entrypoint que `python -m frontend`
sempre usou. Este módulo não duplica nada da inicialização — `.env`,
auto-login, ProviderRouter, voz e encerramento continuam sendo
responsabilidade do launcher.

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
  jarvis start
  jarvis delete all users"""

# Formas aceitas para "inicie o JARVIS". A comparação é feita sobre os
# argumentos unidos por espaço e normalizados (minúsculas, sem espaço
# redundante), então `jarvis   WAKE   UP` também funciona.
_START_COMMANDS = frozenset({"", "wake", "wake up", "start"})
_HELP_FLAGS = frozenset({"-h", "--help", "help"})
_DELETE_ALL_USERS = "delete all users"
# `--yes` pula a confirmação interativa. Existe para desenvolvimento e
# teste; o caminho documentado é o interativo, de propósito — um comando
# destrutivo não deve ter um atalho conveniente em destaque.
_YES_FLAGS = frozenset({"--yes", "-y"})

CONFIRMATION_WORD = "DELETE"
_WARNING = (
    "WARNING: This will permanently delete all JARVIS users, sessions, "
    "chats and user memories."
)


class Command(Enum):
    START = "start"  # abrir o HUD
    HELP = "help"  # ajuda pedida explicitamente (saída 0)
    UNKNOWN = "unknown"  # argumento não reconhecido (ajuda + saída 2)
    DELETE_ALL_USERS = "delete_all_users"


def _normalize(args: list[str]) -> str:
    return " ".join(" ".join(args).lower().split())


def resolve_command(args: list[str]) -> Command:
    """Decide o que fazer com os argumentos. Função pura — nenhum efeito
    colateral, nada de Qt, nada de I/O, nada de banco."""
    normalized = _normalize(args)
    without_yes = _normalize([a for a in args if a.lower() not in _YES_FLAGS])

    if normalized in _START_COMMANDS:
        return Command.START
    if normalized in _HELP_FLAGS:
        return Command.HELP
    if without_yes == _DELETE_ALL_USERS:
        return Command.DELETE_ALL_USERS
    return Command.UNKNOWN


def wants_assume_yes(args: list[str]) -> bool:
    return any(arg.lower() in _YES_FLAGS for arg in args)


def run_delete_all_users(*, assume_yes: bool = False, input_fn=input, settings=None) -> int:
    """Apaga todas as contas locais. `input_fn`/`settings` são injetáveis
    para o teste rodar contra um banco temporário e uma confirmação
    simulada — nunca contra os dados reais."""
    if settings is None:
        from config.settings import settings as default_settings

        settings = default_settings

    if not assume_yes:
        print(_WARNING)
        print()
        try:
            answer = input_fn(f"Type {CONFIRMATION_WORD} to continue: ")
        except (EOFError, KeyboardInterrupt):
            print()
            print("Operation cancelled.")
            return 1
        # Comparação exata e sensível a maiúsculas, de propósito: "delete"
        # ou "y" não podem apagar as contas de alguém sem querer.
        if answer.strip() != CONFIRMATION_WORD:
            print("Operation cancelled.")
            return 1

    from services.user_data_reset import BackupFailedError, delete_all_users

    try:
        summary = delete_all_users(settings)
    except BackupFailedError as exc:
        # Falha de backup nunca apaga nada — é a trava principal.
        print(f"Backup failed: {exc}", file=sys.stderr)
        print("Nothing was deleted.", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - caminho de erro inesperado
        print(f"Failed to delete users: {exc}", file=sys.stderr)
        return 1

    print(f"Backup saved to: {summary.backup_path}")
    for table, count in summary.deleted.items():
        print(f"  {table}: {count} row(s) deleted")
    if summary.memory_dirs_removed:
        print(f"  user memory folders removed: {summary.memory_dirs_removed}")
    print("All JARVIS users removed. The app will start as a fresh install.")
    return 0


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
    if command is Command.DELETE_ALL_USERS:
        return run_delete_all_users(assume_yes=wants_assume_yes(args))

    # Import tardio de propósito: carregar o Qt custa caro, e `jarvis --help`
    # não precisa pagar por isso. Também mantém este módulo importável (e
    # testável) sem tocar na stack gráfica.
    from frontend.launcher import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
