"""Ações locais no Windows: volume, clipboard, screenshot, processos, abrir
coisas (v1.7).

--------------------------------------------------------------------------
Regras que valem para tudo aqui
--------------------------------------------------------------------------
**Nada depende de IA.** Abrir o Spotify é uma chamada ao sistema operacional.
Com todos os providers fora do ar, tudo neste módulo continua funcionando.

**Nada aqui decide se pode.** Autorização, risco e confirmação são de
`app/actions.py`. Este módulo é o "como", e assume que a decisão já foi
tomada — é por isso que ele é pequeno e direto.

**Nada aqui levanta para o chamador.** Toda operação devolve `(ok, detalhe)`.
Uma falha de volume não pode derrubar a Command Bar, e o detalhe é sempre
texto apresentável — nunca stack trace, nunca caminho interno.

**Sem dependência nova.** Volume usa a API de teclas de mídia do próprio
Windows via `ctypes`; processos usam o `tasklist` que já existe; clipboard e
screenshot usam o Qt que o app já carrega. `pycaw` é usado SE estiver
instalado (para volume absoluto), e ignorado se não estiver.
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Teclas de mídia virtuais do Windows. Usar as teclas (e não a API COM de
# áudio) é o caminho sem dependência: é exatamente o que um teclado com
# botão de volume envia, e o Windows aplica ao dispositivo de saída atual.
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF
_KEYEVENTF_KEYUP = 0x0002

# Cada passo de tecla mexe ~2% no Windows. 5 passos ≈ 10%, que é o que uma
# pessoa espera de "aumenta o volume".
_VOLUME_STEPS = 5

# Teto do que é lido do clipboard e devolvido. Clipboard pode conter um
# documento inteiro; devolver tudo para a UI (e potencialmente para a IA, num
# "resume meu clipboard") seria mover muito dado sem necessidade.
_CLIPBOARD_PREVIEW_CHARS = 4000

_MAX_PROCESSES = 12


@dataclass(frozen=True)
class ProcessInfo:
    name: str
    pid: int
    memory_mb: float


class SystemControl:
    """Ações locais. Instanciável sem efeito colateral — nada é aberto,
    medido ou lido no construtor."""

    def __init__(self, *, screenshots_dir: Path | None = None) -> None:
        self._screenshots_dir = screenshots_dir

    # ------------------------------------------------------------------
    # Abrir coisas
    # ------------------------------------------------------------------

    def open_path(self, target: str | Path) -> tuple[bool, str]:
        """Abre arquivo, pasta ou atalho com o programa padrão do sistema.

        `os.startfile` é a forma correta no Windows: respeita a associação de
        arquivo do usuário e não exige montar linha de comando — o que
        elimina de uma vez a classe de bug de aspas em caminho com espaço."""
        path = Path(target)
        if not path.exists():
            return False, f"Não encontrei: {path.name}"
        try:
            os.startfile(str(path))  # noqa: S606 - caminho validado acima
        except OSError as exc:
            logger.info("Falha ao abrir %s: %s", path.name, exc)
            return False, f"Não foi possível abrir {path.name}."
        return True, f"Abrindo {path.stem}"

    def open_url(self, url: str) -> tuple[bool, str]:
        """Abre uma URL no navegador padrão.

        Só `http`/`https`: `file://` abriria arquivo local e outros esquemas
        (`javascript:`, `ms-settings:` com parâmetro) são superfície de abuso
        que uma barra de comando não precisa oferecer."""
        cleaned = (url or "").strip()
        if not cleaned.lower().startswith(("http://", "https://")):
            return False, "Só consigo abrir endereços http ou https."
        import webbrowser

        try:
            opened = webbrowser.open(cleaned)
        except Exception as exc:
            logger.info("Falha ao abrir URL: %s", exc)
            return False, "Não foi possível abrir o navegador."
        return (True, "Abrindo no navegador") if opened else (False, "Nenhum navegador disponível.")

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    def _send_key(self, virtual_key: int, times: int = 1) -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes

            user32 = ctypes.windll.user32
            for _ in range(times):
                user32.keybd_event(virtual_key, 0, 0, 0)
                user32.keybd_event(virtual_key, 0, _KEYEVENTF_KEYUP, 0)
            return True
        except Exception as exc:  # pragma: no cover - depende do ambiente
            logger.info("Falha ao enviar tecla de mídia: %s", exc)
            return False

    def volume_up(self) -> tuple[bool, str]:
        ok = self._send_key(_VK_VOLUME_UP, _VOLUME_STEPS)
        return (True, "Volume aumentado") if ok else (False, "Não foi possível ajustar o volume.")

    def volume_down(self) -> tuple[bool, str]:
        ok = self._send_key(_VK_VOLUME_DOWN, _VOLUME_STEPS)
        return (True, "Volume diminuído") if ok else (False, "Não foi possível ajustar o volume.")

    def mute(self) -> tuple[bool, str]:
        ok = self._send_key(_VK_VOLUME_MUTE)
        return (True, "Áudio silenciado") if ok else (False, "Não foi possível silenciar o áudio.")

    def unmute(self) -> tuple[bool, str]:
        """A tecla de mudo do Windows ALTERNA — não existe "desmutar"
        separado. Honesto sobre isso na mensagem, em vez de fingir que ligou
        o som quando pode ter desligado."""
        ok = self._send_key(_VK_VOLUME_MUTE)
        return (True, "Mudo alternado") if ok else (False, "Não foi possível alterar o áudio.")

    def volume_set(self, level: int) -> tuple[bool, str]:
        """Volume absoluto. Requer a API COM de áudio do Windows, que só está
        disponível com o `pycaw` instalado.

        Sem ele, isto NÃO aproxima com passos de tecla: pedir 30% e receber
        "abaixei um pouco" seria fingir precisão que não existe. A mensagem
        diz o que dá para fazer."""
        level = max(0, min(100, int(level)))
        try:
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        except Exception:
            return (
                False,
                "Ajuste exato de volume não está disponível nesta instalação. "
                "Posso aumentar ou diminuir.",
            )
        try:
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        except Exception as exc:  # pragma: no cover - depende do ambiente
            logger.info("Falha ao definir volume absoluto: %s", exc)
            return False, "Não foi possível ajustar o volume."
        return True, f"Volume em {level}%"

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    def read_clipboard(self) -> tuple[bool, str]:
        """Lê a área de transferência.

        Clipboard é dado potencialmente sensível — pode conter uma senha que
        a pessoa acabou de copiar do gerenciador. Por isso: lido só quando
        pedido explicitamente, nunca persistido, nunca registrado em log, e
        truncado no retorno."""
        try:
            from PySide6.QtGui import QGuiApplication

            clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                return False, "Área de transferência indisponível."
            text = clipboard.text() or ""
        except Exception as exc:
            logger.info("Falha ao ler a área de transferência: %s", exc)
            return False, "Não foi possível ler a área de transferência."

        if not text.strip():
            return True, "A área de transferência está vazia."
        # O CONTEÚDO nunca vai para o log — só o tamanho.
        logger.info("Área de transferência lida (%s caracteres).", len(text))
        return True, text[:_CLIPBOARD_PREVIEW_CHARS]

    def write_clipboard(self, text: str) -> tuple[bool, str]:
        try:
            from PySide6.QtGui import QGuiApplication

            clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                return False, "Área de transferência indisponível."
            clipboard.setText(text or "")
        except Exception as exc:
            logger.info("Falha ao escrever na área de transferência: %s", exc)
            return False, "Não foi possível copiar."
        return True, "Copiado"

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def screenshot(self) -> tuple[bool, str]:
        """Captura a tela e salva em disco.

        A imagem NÃO é enviada para a IA nem para lugar nenhum: fica no disco
        do usuário, e o que volta é o caminho. Mandar um print para um
        provider sem pedido explícito seria enviar para fora tudo que estava
        na tela."""
        target_dir = self._screenshots_dir
        if target_dir is None:
            from config.settings import settings

            target_dir = settings.data_dir / "screenshots"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.info("Falha ao criar a pasta de screenshots: %s", exc)
            return False, "Não foi possível salvar a captura."

        destination = target_dir / f"jarvis-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        try:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return False, "Nenhuma tela disponível para captura."
            pixmap = screen.grabWindow(0)
            if not pixmap.save(str(destination), "PNG"):
                return False, "Não foi possível salvar a captura."
        except Exception as exc:
            logger.info("Falha ao capturar a tela: %s", exc)
            return False, "Não foi possível capturar a tela."
        return True, str(destination)

    # ------------------------------------------------------------------
    # Processos e sistema
    # ------------------------------------------------------------------

    def list_processes(self, *, limit: int = _MAX_PROCESSES) -> tuple[bool, list[ProcessInfo]]:
        """Processos que mais consomem memória.

        Usa o `tasklist` do próprio Windows em vez de somar uma dependência —
        e deliberadamente só LÊ. Esta versão não encerra processo por aqui:
        fechar programa é `close_app`, que passa por confirmação explícita."""
        if os.name != "nt":
            return False, []
        try:
            completed = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.info("Falha ao listar processos: %s", exc)
            return False, []
        if completed.returncode != 0:
            return False, []

        import csv
        import io

        processes: list[ProcessInfo] = []
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) < 5:
                continue
            name, pid_text, _session, _session_number, memory_text = row[:5]
            try:
                pid = int(pid_text)
                # "12.345 K" -> 12345 KB. O separador é o do locale do
                # Windows, então remover tudo que não é dígito é mais robusto
                # que presumir vírgula ou ponto.
                kilobytes = int("".join(ch for ch in memory_text if ch.isdigit()) or 0)
            except ValueError:
                continue
            processes.append(ProcessInfo(name=name, pid=pid, memory_mb=kilobytes / 1024))

        processes.sort(key=lambda item: item.memory_mb, reverse=True)
        return True, processes[:limit]

    def close_app(self, name: str) -> tuple[bool, str]:
        """Encerra um programa pelo nome da imagem.

        Ação de risco ALTO (ver `app/actions.py`): pode descartar trabalho não
        salvo. Só chega aqui depois de confirmação explícita.

        `/IM` sem `/F`: pede o encerramento normal, que dá ao programa a
        chance de perguntar "salvar alterações?". Forçar com `/F` mataria o
        processo e o trabalho junto."""
        image = (name or "").strip()
        if not image:
            return False, "Nenhum programa informado."
        if not image.lower().endswith(".exe"):
            image = f"{image}.exe"
        if os.name != "nt":
            return False, "Encerrar programas só está disponível no Windows."
        try:
            completed = subprocess.run(
                ["taskkill", "/IM", image],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.info("Falha ao encerrar %s: %s", image, exc)
            return False, "Não foi possível encerrar o programa."
        if completed.returncode != 0:
            return False, f"{Path(image).stem} não parece estar em execução."
        return True, f"{Path(image).stem} encerrado"

    def system_info(self) -> tuple[bool, dict[str, str]]:
        """Informações básicas da máquina. Só o que já está disponível no
        ambiente — nada é medido invasivamente, e nada disto sai da máquina."""
        import platform

        info = {
            "sistema": f"{platform.system()} {platform.release()}",
            "arquitetura": platform.machine(),
            "processadores": str(os.cpu_count() or "?"),
            "python": platform.python_version(),
        }
        try:
            usage = os.statvfs(sys.executable) if hasattr(os, "statvfs") else None
            if usage is not None:  # pragma: no cover - não-Windows
                info["disco_livre_gb"] = f"{usage.f_bavail * usage.f_frsize / 1024**3:.0f}"
        except OSError:
            pass
        return True, info
