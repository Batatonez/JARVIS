"""As skills que existem hoje (v1.8).

Cada uma é um ADAPTADOR sobre um serviço que já existia — não uma segunda
implementação:

    CalculatorSkill  ->  app/command_bar.py::_safe_eval
    SystemSkill      ->  services/system/system_control.py
    FilesSkill       ->  services/files/file_search.py

Se uma skill precisasse reimplementar a lógica do serviço, a camada de
skills estaria criando o problema que veio resolver: duas versões da mesma
regra, divergindo com o tempo.

Nenhuma skill de shell arbitrário existe aqui, por desenho — ver o cabeçalho
de `app/skills.py`.
"""

import logging
from pathlib import Path

from app.models import RiskLevel
from app.skills import Skill, SkillAction, SkillError, SkillParameter, SkillResult

logger = logging.getLogger(__name__)


def build_calculator_skill() -> Skill:
    """Aritmética. Reusa `_safe_eval`, o interpretador de AST da v1.7 — que
    não é `eval` restrito e não alcança nenhum objeto Python."""
    from app.command_bar import _safe_eval

    def calculate(expression: str) -> SkillResult:
        value = _safe_eval(expression)
        if value is None:
            return SkillResult(ok=False, detail="Não consegui calcular isso.")
        formatted = str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{value:.4f}".rstrip("0").rstrip(".")
        return SkillResult(ok=True, detail=formatted, data={"value": value})

    return Skill(
        id="calculator",
        name="Calculadora",
        description="Resolve expressões aritméticas.",
        actions={
            "evaluate": SkillAction(
                name="evaluate",
                description="Calcular uma expressão aritmética",
                parameters=(
                    SkillParameter("expression", "string", True, "Ex.: 15*3 ou (10+5)/3"),
                ),
                risk_level=RiskLevel.READ,
                handler=calculate,
            )
        },
    )


def build_system_skill(system=None) -> Skill:
    """Controle do computador. Reusa `SystemControl` — nenhuma chamada de
    sistema é reimplementada aqui."""
    if system is None:
        from services.system.system_control import SystemControl

        system = SystemControl()

    def _wrap(ok: bool, detail: str) -> SkillResult:
        return SkillResult(ok=ok, detail=detail)

    def volume_up() -> SkillResult:
        return _wrap(*system.volume_up())

    def volume_down() -> SkillResult:
        return _wrap(*system.volume_down())

    def mute() -> SkillResult:
        return _wrap(*system.mute())

    def screenshot() -> SkillResult:
        ok, detail = system.screenshot()
        # O caminho vai em `data`, não no texto: a UI usa para as quick
        # actions, e o usuário não precisa ler um caminho absoluto na tela.
        return SkillResult(ok=ok, detail="Captura salva" if ok else detail,
                           data={"path": detail} if ok else {})

    def list_processes() -> SkillResult:
        ok, processes = system.list_processes()
        if not ok:
            return SkillResult(ok=False, detail="Não foi possível listar os processos.")
        lines = [f"{item.name} — {item.memory_mb:.0f} MB" for item in processes]
        return SkillResult(ok=True, detail="\n".join(lines))

    def read_clipboard() -> SkillResult:
        return _wrap(*system.read_clipboard())

    def close_app(name: str) -> SkillResult:
        return _wrap(*system.close_app(name))

    import os

    return Skill(
        id="system",
        name="Sistema",
        description="Volume, área de transferência, capturas e processos.",
        # Fora do Windows praticamente nada aqui funciona; declarar
        # indisponível é mais honesto que oferecer e falhar.
        available=lambda: os.name == "nt",
        actions={
            "volume_up": SkillAction("volume_up", "Aumentar o volume", (), RiskLevel.ACTION, "system_control", volume_up),
            "volume_down": SkillAction("volume_down", "Diminuir o volume", (), RiskLevel.ACTION, "system_control", volume_down),
            "mute": SkillAction("mute", "Silenciar o áudio", (), RiskLevel.ACTION, "system_control", mute),
            "screenshot": SkillAction("screenshot", "Capturar a tela", (), RiskLevel.ACTION, "screenshots", screenshot),
            "list_processes": SkillAction("list_processes", "Listar processos em execução", (), RiskLevel.READ, "system_control", list_processes),
            "read_clipboard": SkillAction("read_clipboard", "Ler a área de transferência", (), RiskLevel.READ, "clipboard", read_clipboard),
            # Risco alto: pode descartar trabalho não salvo. Sempre confirma.
            "close_app": SkillAction(
                "close_app", "Fechar um programa",
                (SkillParameter("name", "string", True, "Nome do programa"),),
                RiskLevel.DANGEROUS, "applications", close_app,
            ),
        },
    )


def build_files_skill(search_service=None, system=None) -> Skill:
    """Busca e ações de arquivo. Reusa `FileSearchService` e `SystemControl`.

    **Todas as ações sobre arquivo usam HANDLE, nunca caminho.** `open`
    recebe `file_2`; um caminho absoluto — venha de onde vier, inclusive de
    uma instrução escondida dentro de um documento — não é aceito. Isso
    elimina a classe de injeção de caminho em uso de ferramenta."""
    if system is None:
        from services.system.system_control import SystemControl

        system = SystemControl()

    def _resolve(handle: str) -> Path:
        if search_service is None:
            raise SkillError("A busca de arquivos não está disponível.")
        path = search_service.resolve_handle(handle)
        if path is None:
            # Handle inválido falha SEGURO: não tenta interpretar como
            # caminho, não procura parecido, não adivinha.
            raise SkillError("Esse resultado não está mais disponível. Faça a busca de novo.")
        if not path.exists():
            raise SkillError("Esse arquivo não existe mais.")
        return path

    def search(query: str, limit: int = 8) -> SkillResult:
        if search_service is None:
            return SkillResult(ok=False, detail="A busca de arquivos não está disponível.")
        results = search_service.search(query, limit=limit)
        if not results:
            return SkillResult(ok=True, detail="Nenhum arquivo encontrado.", data={"results": []})
        return SkillResult(
            ok=True,
            detail=f"{len(results)} arquivo(s) encontrado(s).",
            data={"results": [item.to_view() for item in results]},
        )

    def recent(limit: int = 8) -> SkillResult:
        if search_service is None:
            return SkillResult(ok=False, detail="A busca de arquivos não está disponível.")
        results = search_service.recent(limit=limit)
        return SkillResult(
            ok=True,
            # "modificados", não "abertos": o índice sabe o mtime do sistema
            # de arquivos e nada além disso.
            detail=f"{len(results)} arquivo(s) modificado(s) recentemente.",
            data={"results": [item.to_view() for item in results]},
        )

    def open_file(handle: str) -> SkillResult:
        path = _resolve(handle)
        ok, detail = system.open_path(path)
        return SkillResult(ok=ok, detail=detail)

    def show_in_folder(handle: str) -> SkillResult:
        path = _resolve(handle)
        ok, detail = system.show_in_folder(path)
        return SkillResult(ok=ok, detail=detail)

    def copy_path(handle: str) -> SkillResult:
        path = _resolve(handle)
        ok, _detail = system.write_clipboard(str(path))
        # O caminho vai para a área de transferência, não para o log.
        return SkillResult(ok=ok, detail="Caminho copiado" if ok else "Não foi possível copiar.")

    def read_for_summary(handle: str) -> SkillResult:
        """Extrai o texto para um resumo. NÃO chama IA — devolve o conteúdo
        para quem pediu decidir o que fazer. A fronteira com o provider fica
        em um lugar só, e visível."""
        from services.files.content_extractor import summary_input

        path = _resolve(handle)
        text, reason = summary_input(path)
        if text is None:
            return SkillResult(ok=False, detail=reason)
        return SkillResult(ok=True, detail="", data={"text": text, "name": path.name})

    return Skill(
        id="files",
        name="Arquivos",
        description="Encontra, abre e resume arquivos do computador.",
        available=lambda: search_service is not None,
        actions={
            "search": SkillAction(
                "search", "Buscar arquivos por nome, tipo ou conteúdo",
                (SkillParameter("query", "string", True, "O que procurar"),
                 SkillParameter("limit", "integer", False, "Máximo de resultados")),
                RiskLevel.READ, "files", search,
            ),
            "recent": SkillAction(
                "recent", "Listar arquivos modificados recentemente",
                (SkillParameter("limit", "integer", False, "Máximo de resultados"),),
                RiskLevel.READ, "files", recent,
            ),
            "open": SkillAction(
                "open", "Abrir um arquivo encontrado",
                (SkillParameter("handle", "string", True, "Identificador do resultado, ex.: file_1"),),
                RiskLevel.ACTION, "files", open_file,
            ),
            "show_in_folder": SkillAction(
                "show_in_folder", "Mostrar o arquivo na pasta",
                (SkillParameter("handle", "string", True, "Identificador do resultado"),),
                RiskLevel.ACTION, "files", show_in_folder,
            ),
            "copy_path": SkillAction(
                "copy_path", "Copiar o caminho do arquivo",
                (SkillParameter("handle", "string", True, "Identificador do resultado"),),
                RiskLevel.READ, "files", copy_path,
            ),
            "read_for_summary": SkillAction(
                "read_for_summary", "Ler o conteúdo de um arquivo para resumir",
                (SkillParameter("handle", "string", True, "Identificador do resultado"),),
                RiskLevel.READ, "files", read_for_summary,
            ),
        },
    )


def build_default_registry(*, search_service=None, system=None):
    """Registro com as skills reais. Nenhuma classe vazia registrada só para
    a arquitetura parecer completa — uma skill que não faz nada é ruído no
    catálogo que um modelo leria."""
    from app.skills import SkillRegistry

    registry = SkillRegistry()
    registry.register(build_calculator_skill())
    registry.register(build_system_skill(system=system))
    registry.register(build_files_skill(search_service=search_service, system=system))
    return registry
