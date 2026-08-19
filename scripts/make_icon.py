#!/usr/bin/env python
"""Gera o ícone do Windows a partir do logo do JARVIS.

    python scripts/make_icon.py "JARVIS logo.png"
    python scripts/make_icon.py                    # procura o logo padrão

Produz `packaging/windows/assets/jarvis.ico` com todas as resoluções que o
Windows pede em contextos diferentes — 16px na barra de título, 32px na barra
de tarefas, 48px no Explorer, 256px na visualização grande. Um `.ico` com um
tamanho só é reamostrado pelo Windows na hora, e o resultado em 16px fica
borrado.

--------------------------------------------------------------------------
Por que um script, e por que ele não roda no build
--------------------------------------------------------------------------
O `.ico` é VERSIONADO. Ele muda quando a arte muda, o que é raro, e gerá-lo
a cada build somaria o Pillow às dependências de build de todo mundo por uma
conversão que acontece uma vez por ano.

Então: rode isto quando o logo mudar, commite o `.ico` gerado, e o build
continua sem precisar do Pillow (ver `packaging/windows/jarvis.spec`, que só
consome o arquivo se ele existir).

O PNG de origem NÃO é versionado (ver `.gitignore`): é um arquivo grande de
arte, e o que o repositório precisa é do resultado.

--------------------------------------------------------------------------
A arte não é alterada
--------------------------------------------------------------------------
Este script só REDIMENSIONA. Não recorta, não remove fundo, não ajusta cor,
não adiciona borda. Se o logo tem fundo escuro opaco, o ícone terá fundo
escuro opaco — transformar isso em transparência seria uma decisão de design,
e não é de um script de conversão.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = ("JARVIS logo.png", "jarvis-logo.png", "logo.png")
OUTPUT = PROJECT_ROOT / "packaging" / "windows" / "assets" / "jarvis.ico"

# Tamanhos que o Windows realmente usa. 20/24/40 entram porque aparecem em
# DPI escalonado (125%, 150%) — sem eles, o Windows reamostra do vizinho mais
# próximo e o ícone fica visivelmente pior nessas escalas.
SIZES = [(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)]


def find_source(argument: str | None) -> Path | None:
    if argument:
        candidate = Path(argument)
        return candidate if candidate.is_file() else None
    for name in DEFAULT_SOURCES:
        candidate = PROJECT_ROOT / name
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print(
            "Pillow é necessário para gerar o ícone (só para isto — o build não precisa dele).\n"
            "    pip install Pillow",
            file=sys.stderr,
        )
        return 1

    source = find_source(sys.argv[1] if len(sys.argv) > 1 else None)
    if source is None:
        print(
            "Logo não encontrado. Passe o caminho:\n"
            '    python scripts/make_icon.py "JARVIS logo.png"',
            file=sys.stderr,
        )
        return 1

    image = Image.open(source)
    # RGBA mesmo quando a origem é RGB: o formato ICO espera canal alfa, e
    # converter aqui evita que o Windows invente um.
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    width, height = image.size
    if width != height:
        # Quadrado por recorte CENTRAL: um ícone não-quadrado é esticado pelo
        # Windows, o que distorce a arte. O recorte é a distorção menor.
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        image = image.crop((left, top, left + side, top + side))
        print(f"Aviso: logo não era quadrado ({width}x{height}); recortado no centro para {side}x{side}.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # `LANCZOS` é o reamostrador de melhor qualidade do Pillow para reduzir —
    # importa muito em 16px, onde um filtro pior vira um borrão.
    image.save(OUTPUT, format="ICO", sizes=SIZES)

    print(f"Ícone gerado: {OUTPUT}")
    print(f"  origem:    {source}  ({width}x{height})")
    print(f"  resoluções: {', '.join(str(size) for size, _ in SIZES)}")
    print(f"  tamanho:   {OUTPUT.stat().st_size / 1024:.0f} KB")
    print("\nCommite o .ico. O build o usa automaticamente e não precisa do Pillow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
