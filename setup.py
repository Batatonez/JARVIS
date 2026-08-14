#!/usr/bin/env python
"""Preparação do JARVIS em um comando:

    python setup.py

Instala as dependências, instala o JARVIS em modo editável (registrando o
comando `jarvis`), baixa o modelo de reconhecimento de fala se necessário e
verifica o microfone. Idempotente: rodar de novo não baixa nada que já
esteja íntegro.

---------------------------------------------------------------------------
POR QUE ESTE ARQUIVO TEM DOIS MODOS

`setup.py` é, historicamente, o script de *build* que o pip executa
(`setup.py egg_info`, `bdist_wheel`, ...). Como o `pyproject.toml` deste
projeto declara `setuptools.build_meta` como backend, o setuptools ainda
executa este arquivo durante `pip install -e .`. Se ele fizesse a
preparação (rodar pip, baixar 45 MB) nesse contexto, um simples
`pip install -e .` dispararia tudo isso — ou entraria em recursão.

A distinção é o `argv`: o pip/setuptools SEMPRE passa um comando
(`sys.argv[1:]` não vazio); o usuário digita `python setup.py` sem
argumentos. Com argumento -> delega ao setuptools (build normal, metadados
lidos do pyproject.toml). Sem argumento -> roda a preparação.
---------------------------------------------------------------------------
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _delegate_to_setuptools() -> None:
    """Build normal (pip/setuptools). Sem argumentos próprios: os metadados
    vêm do `[project]` do pyproject.toml."""
    from setuptools import setup

    setup()


# ---------------------------------------------------------------------------
# Preparação (só quando executado diretamente pelo usuário)
# ---------------------------------------------------------------------------

_LINE = "-" * 62


def _run_pip(*args: str) -> tuple[bool, str]:
    """pip no MESMO interpretador que está rodando este script — evita o
    clássico de instalar num Python e rodar em outro."""
    command = [sys.executable, "-m", "pip", *args]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        return (False, str(exc))
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return (False, tail[-1] if tail else f"pip saiu com código {completed.returncode}")
    return (True, "")


def _install_dependencies() -> "StepResult":
    from services.first_run_setup import StepResult, StepStatus

    requirements = PROJECT_ROOT / "requirements.txt"
    if not requirements.is_file():
        return StepResult(StepStatus.FAILED, "requirements.txt não encontrado")

    print("[1/4] Instalando dependências (requirements.txt)...")
    ok, error = _run_pip("install", "-r", str(requirements))
    if not ok:
        return StepResult(StepStatus.FAILED, error)
    return StepResult(StepStatus.OK, "requirements.txt")


def _install_launcher() -> "StepResult":
    from services.first_run_setup import StepResult, StepStatus

    print("[2/4] Instalando o JARVIS e o comando `jarvis`...")
    # `--no-deps`: as dependências já vieram do requirements.txt, que é a
    # fonte única de versões. Sem isto, o pip resolveria de novo o que já
    # está satisfeito (e o pyproject nem declara dependências, ver lá).
    ok, error = _run_pip("install", "-e", str(PROJECT_ROOT), "--no-deps")
    if not ok:
        return StepResult(StepStatus.FAILED, error)
    return StepResult(StepStatus.OK, "comando `jarvis` registrado")


def _progress_printer():
    """Barra de progresso simples, em uma linha só."""
    state = {"last": -1}

    def _on_progress(downloaded: int, total: int) -> None:
        if total <= 0:
            return
        percent = int(downloaded * 100 / total)
        if percent == state["last"]:
            return
        state["last"] = percent
        filled = percent * 30 // 100
        bar = "#" * filled + "." * (30 - filled)
        print(
            f"\r      [{bar}] {percent:3d}%  "
            f"({downloaded / 1_000_000:.1f}/{total / 1_000_000:.1f} MB)",
            end="",
            flush=True,
        )

    return _on_progress


def _setup_speech_to_text() -> tuple["StepResult", str, str]:
    """Prepara os DOIS engines (v1.3) e devolve
    `(resultado, engine_principal, fallback)`.

    Política do item 7/10: o faster-whisper é o principal e o Vosk é o
    fallback leve. O STT conta como pronto se **pelo menos um** estiver
    utilizável — uma máquina sem espaço para o Whisper continua com voz
    funcionando pelo Vosk. Modelo já instalado e válido nunca é rebaixado."""
    from config.settings import settings
    from services.first_run_setup import (
        StepResult,
        StepStatus,
        build_model_manager,
        build_whisper_manager,
        check_whisper_dependencies,
        ensure_stt_model,
        ensure_whisper_model,
    )
    from services.vosk_model_manager import VoiceModelManager

    engine = "—"
    fallback = "—"

    # --- Engine principal: faster-whisper ---
    whisper_result = None
    whisper_dependencies = check_whisper_dependencies()
    if whisper_dependencies.ok:
        whisper = build_whisper_manager(settings)
        if whisper.is_installed:
            print("[3/4] Modelo do Faster Whisper: já instalado.")
            whisper_result = ensure_whisper_model(whisper, allow_download=False)
        else:
            info = whisper.info()
            approximate_mb = info.approximate_size_bytes / 1_000_000
            print("[3/4] Speech-to-text model not found (Faster Whisper).")
            print(f"      Baixando o modelo '{info.size}' (~{approximate_mb:.0f} MB)...")
            print(f"      Idioma: {info.language} | Licença: {info.license}")
            print(f"      Origem: {info.source}")
            print(f"      Destino: {whisper.model_path}")
            whisper_result = ensure_whisper_model(whisper, on_progress=_progress_printer())
            print()  # fecha a linha da barra de progresso
        if whisper_result.ok:
            engine = "Faster Whisper"
    else:
        print(f"[3/4] Faster Whisper indisponível ({whisper_dependencies.detail}).")
        print("      O JARVIS vai usar o Vosk como engine de voz.")

    # --- Fallback: Vosk ---
    vosk_manager = build_model_manager(settings)
    if vosk_manager.is_complete:
        print("      Modelo Vosk (fallback): já instalado.")
        vosk_result = ensure_stt_model(vosk_manager, allow_download=False)
    else:
        info = VoiceModelManager.info()
        print(f"      Baixando o modelo Vosk de fallback (~{info.approximate_size_bytes / 1_000_000:.0f} MB)...")
        vosk_result = ensure_stt_model(vosk_manager, on_progress=_progress_printer())
        print()
    if vosk_result.ok:
        fallback = "Vosk"
        if engine == "—":
            engine = "Vosk"
            fallback = "—"

    # Pronto se qualquer um dos dois funciona.
    if whisper_result is not None and whisper_result.ok:
        return whisper_result, engine, fallback
    if vosk_result.ok:
        return vosk_result, engine, fallback
    detail = whisper_result.detail if whisper_result is not None else vosk_result.detail
    return StepResult(StepStatus.FAILED, detail), engine, fallback


def _check_microphone() -> "StepResult":
    from services.first_run_setup import detect_microphone

    print("[4/4] Verificando dispositivo de entrada...")
    return detect_microphone()


def _format_line(label: str, value: str) -> str:
    return f"{label} {'.' * max(3, 20 - len(label))} {value}"


def main() -> int:
    # Import tardio: `services.*` só é importável depois de o próprio
    # projeto estar no path, e queremos falhar com mensagem clara se as
    # dependências ainda não existirem.
    sys.path.insert(0, str(PROJECT_ROOT))
    from services.first_run_setup import SetupReport, StepStatus, check_voice_dependencies

    print()
    print("JARVIS SETUP")
    print(_LINE)

    dependencies = _install_dependencies()
    if dependencies.ok:
        # Confere que o que a voz precisa ficou realmente importável.
        voice_dependencies = check_voice_dependencies()
        if not voice_dependencies.ok:
            dependencies = voice_dependencies

    launcher = _install_launcher() if dependencies.ok else None
    if launcher is None:
        from services.first_run_setup import StepResult

        launcher = StepResult(StepStatus.SKIPPED, "dependências falharam")

    if dependencies.ok:
        speech, engine, fallback = _setup_speech_to_text()
    else:
        from services.first_run_setup import StepResult

        speech = StepResult(StepStatus.SKIPPED, "dependências falharam")
        engine, fallback = "—", "—"

    microphone = _check_microphone()

    report = SetupReport(
        launcher=launcher,
        dependencies=dependencies,
        speech_to_text=speech,
        microphone=microphone,
        engine=engine,
        fallback=fallback,
    )

    print()
    print(_LINE)
    print("JARVIS SETUP COMPLETE" if report.succeeded else "JARVIS SETUP INCOMPLETE")
    print()
    _STATUS_LABEL = {
        StepStatus.OK: "OK",
        StepStatus.READY: "READY",
        StepStatus.ALREADY_PRESENT: "READY",
        StepStatus.DOWNLOADED: "READY",
        StepStatus.DETECTED: "DETECTED",
        StepStatus.NOT_DETECTED: "NOT DETECTED",
        StepStatus.MISSING: "MISSING",
        StepStatus.FAILED: "FAILED",
        StepStatus.SKIPPED: "SKIPPED",
    }
    print(_format_line("Launcher", _STATUS_LABEL[report.launcher.status]))
    print(_format_line("Dependencies", _STATUS_LABEL[report.dependencies.status]))
    print(_format_line("Speech-to-Text", _STATUS_LABEL[report.speech_to_text.status]))
    print(_format_line("Engine", report.engine))
    print(_format_line("Fallback", report.fallback))
    microphone_value = (
        report.microphone.detail
        if report.microphone.status is StepStatus.DETECTED and report.microphone.detail
        else _STATUS_LABEL[report.microphone.status]
    )
    print(_format_line("Microphone", microphone_value))

    for label, step in (
        ("Launcher", report.launcher),
        ("Dependencies", report.dependencies),
        ("Speech-to-Text", report.speech_to_text),
        ("Microphone", report.microphone),
    ):
        if step.detail and step.status is not StepStatus.OK:
            print(f"  - {label}: {step.detail}")

    if report.microphone.status is StepStatus.NOT_DETECTED:
        print()
        print("  Nenhum microfone detectado. O JARVIS funciona normalmente por")
        print("  texto; conecte um microfone e reabra o app para usar a voz.")

    print()
    if report.succeeded:
        print("Open a new terminal and run:")
        print()
        print("    jarvis")
        print()
        print("(terminal novo: o PATH do atual foi lido antes da instalação)")
    else:
        print("Corrija os itens acima e rode `python setup.py` novamente.")
    print()
    return 0 if report.succeeded else 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # pip/setuptools passando um comando de build.
        _delegate_to_setuptools()
    else:
        sys.exit(main())
