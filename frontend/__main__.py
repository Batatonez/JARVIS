"""Ponto de entrada da GUI do JARVIS.

Uso:
    python -m frontend

O terminal (`python main.py`) continua sendo um caminho separado e
independente — nada aqui o modifica.
"""

import sys

from frontend.launcher import run

if __name__ == "__main__":
    sys.exit(run())
