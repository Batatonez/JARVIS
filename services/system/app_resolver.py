"""Descoberta de aplicativos instalados no Windows (v1.7).

--------------------------------------------------------------------------
Por que não uma lista fixa
--------------------------------------------------------------------------
Uma lista com "spotify", "discord", "chrome" cobre cinco casos e falha no
sexto. O que a pessoa tem instalado é dela, e o JARVIS precisa achar o que
existe naquela máquina — inclusive coisas que ninguém previu.

--------------------------------------------------------------------------
Por que não varrer o disco
--------------------------------------------------------------------------
Percorrer `C:\\` a cada "abrir Spotify" levaria dezenas de segundos, faria o
disco trabalhar à toa e ainda encontraria desinstaladores e binários
auxiliares junto com o que interessa.

A fonte usada é o **Menu Iniciar** — a lista que o próprio Windows mantém do
que está instalado e é para ser aberto. É pequena (centenas de atalhos),
barata de ler, e já exclui binário interno: o que não tem atalho no Menu
Iniciar normalmente não é um aplicativo que se abre pelo nome.

O `PATH` entra como fonte secundária, para comandos como `code` que muita
gente chama pelo nome do executável.

O índice é construído uma vez e reusado; `refresh()` reconstrói quando o
usuário instala algo novo. Um índice frio custa ~100ms nesta máquina.

--------------------------------------------------------------------------
Correspondência
--------------------------------------------------------------------------
Exata > começa com > contém. "code" precisa achar "Visual Studio Code" sem
que "Discord" (que contém "cod"... não contém, mas "Code" casaria por
substring em outros nomes) ganhe de um casamento melhor. Empate é resolvido
pelo nome mais curto: entre "Spotify" e "Spotify Web Helper", quem pediu
"spotify" quer o primeiro.
"""

import logging
import os
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Extensões que representam "algo que se abre". `.lnk` é o atalho do Windows;
# `.url` é um atalho de site (Menu Iniciar tem vários).
_LAUNCHABLE_SUFFIXES = (".lnk", ".url", ".exe")

# Nomes que aparecem no Menu Iniciar e nunca são o que alguém quer abrir por
# voz ou por comando. Filtrar aqui evita "abrir word" resolver para
# "Desinstalar Microsoft Word".
_NOISE_TOKENS = (
    "uninstall", "desinstalar", "remove ", "readme", "leia-me", "help", "ajuda",
    "documentation", "documentação", "license", "licença", "changelog",
    "website", "web site", "homepage", "release notes", "repair", "reparar",
)

_MAX_RESULTS = 8


@dataclass(frozen=True)
class ResolvedApp:
    """Um aplicativo que pode ser aberto. `path` é o que será entregue ao
    sistema operacional; `name` é o que a UI mostra."""

    name: str
    path: Path
    source: str  # "start_menu" | "path"
    # Outros nomes pelos quais este aplicativo é procurado. Existe porque o
    # rótulo bonito e o nome que a pessoa digita raramente coincidem: o
    # Windows chama de "Bloco de Notas", e todo mundo digita "notepad".
    aliases: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return self.name

    def match_names(self) -> tuple[str, ...]:
        return (self.name,) + self.aliases


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.lower().split())


def _is_noise(name: str) -> bool:
    lowered = _normalize(name)
    return any(token in lowered for token in _NOISE_TOKENS)


def _start_menu_dirs() -> list[Path]:
    """Menu Iniciar do usuário e o de todos os usuários. Os dois existem e
    guardam coisas diferentes: instalação por usuário vai para o primeiro,
    instalação para a máquina inteira vai para o segundo."""
    directories = []
    appdata = os.environ.get("APPDATA", "").strip()
    program_data = os.environ.get("PROGRAMDATA", "").strip()
    if appdata:
        directories.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if program_data:
        directories.append(Path(program_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in directories if d.is_dir()]


class AppResolver:
    """Índice de aplicativos, construído sob demanda e reusado."""

    def __init__(self, *, start_menu_dirs: list[Path] | None = None) -> None:
        """`start_menu_dirs` é injetável para os testes usarem um diretório
        temporário com atalhos falsos — nunca para produção passar caminho de
        fora."""
        self._explicit_dirs = start_menu_dirs
        self._index: list[ResolvedApp] | None = None

    # ------------------------------------------------------------------

    def refresh(self) -> int:
        """Reconstrói o índice e devolve quantos aplicativos foram achados.
        Chamado quando o usuário instala algo e o JARVIS ainda não o vê."""
        self._index = self._build_index()
        logger.info("Índice de aplicativos reconstruído: %s entradas.", len(self._index))
        return len(self._index)

    def all_apps(self) -> list[ResolvedApp]:
        if self._index is None:
            self._index = self._build_index()
        return list(self._index)

    def resolve(self, query: str) -> ResolvedApp | None:
        """Melhor correspondência para `query`, ou `None`."""
        matches = self.search(query)
        return matches[0] if matches else None

    def search(self, query: str, *, limit: int = _MAX_RESULTS) -> list[ResolvedApp]:
        """Candidatos ordenados por qualidade do casamento.

        Devolver uma LISTA (e não só o melhor) é o que permite a Command Bar
        mostrar alternativas quando o casamento é ambíguo, em vez de abrir o
        aplicativo errado com confiança."""
        needle = _normalize(query)
        if not needle:
            return []

        exact: list[ResolvedApp] = []
        prefix: list[ResolvedApp] = []
        contains: list[ResolvedApp] = []

        for app in self.all_apps():
            names = [_normalize(candidate) for candidate in app.match_names()]
            if any(name == needle for name in names):
                exact.append(app)
            elif any(name.startswith(needle) for name in names):
                prefix.append(app)
            elif any(needle in name for name in names):
                contains.append(app)

        # Nome mais curto primeiro dentro de cada faixa: entre "Spotify" e
        # "Spotify Web Helper", quem digitou "spotify" quer o primeiro.
        for bucket in (exact, prefix, contains):
            bucket.sort(key=lambda item: (len(item.name), item.name.lower()))

        return (exact + prefix + contains)[:limit]

    # ------------------------------------------------------------------

    def _build_index(self) -> list[ResolvedApp]:
        found: dict[str, ResolvedApp] = {}

        for directory in (self._explicit_dirs if self._explicit_dirs is not None else _start_menu_dirs()):
            for path in self._iter_shortcuts(directory):
                name = path.stem
                if _is_noise(name):
                    continue
                key = _normalize(name)
                # Primeiro que aparece vence: o Menu Iniciar do usuário é
                # lido antes do da máquina, e a versão do usuário é a mais
                # específica.
                found.setdefault(key, ResolvedApp(name=name, path=path, source="start_menu"))

        for app in self._path_executables():
            found.setdefault(_normalize(app.name), app)

        return sorted(found.values(), key=lambda item: item.name.lower())

    @staticmethod
    def _iter_shortcuts(directory: Path):
        try:
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix.lower() in _LAUNCHABLE_SUFFIXES:
                    yield path
        except OSError:
            # Uma pasta ilegível do Menu Iniciar não pode derrubar a
            # descoberta inteira.
            logger.debug("Não foi possível ler %s ao indexar aplicativos.", directory)

    @staticmethod
    def _path_executables() -> list[ResolvedApp]:
        """Executáveis conhecidos que costumam ser chamados pelo nome do
        comando. Lista curta e explícita de propósito: varrer o `PATH` inteiro
        traria dezenas de utilitários de linha de comando que ninguém quer
        "abrir"."""
        apps = []
        for command, label, aliases in (
            ("code", "Visual Studio Code", ("code", "vscode", "vs code")),
            ("notepad", "Bloco de Notas", ("notepad",)),
            ("explorer", "Explorador de Arquivos", ("explorer", "explorador")),
            ("calc", "Calculadora", ("calc", "calculator")),
            ("mspaint", "Paint", ("mspaint", "paint")),
        ):
            location = shutil.which(command)
            if location:
                apps.append(
                    ResolvedApp(name=label, path=Path(location), source="path", aliases=aliases)
                )
        return apps
