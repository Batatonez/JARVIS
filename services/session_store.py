"""Persistência local do token de sessão — só o token opaco (nunca a
senha), para "continuar logado" entre execuções do JARVIS sem reautenticar.

Investigado (item 17 do pedido): o Windows tem DPAPI
(`CryptProtectData`/`CryptUnprotectData`, via `win32crypt` — parte do
`pywin32`, já uma dependência transitiva de `pyttsx3` nesta máquina) — usa a
chave mestra do próprio usuário do Windows para cifrar dados locais, sem o
JARVIS precisar inventar/gerenciar nenhuma criptografia própria. Quando
disponível, o token é gravado cifrado por DPAPI; se `win32crypt` não estiver
instalado (ex.: ambiente sem voz, ou não-Windows), cai para gravar o token
em texto puro — degradação aceitável porque o arquivo guarda só um bearer
token de sessão (não a senha, não o hash), e continua fora do Git.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import win32crypt

    _DPAPI_AVAILABLE = True
except ImportError:
    _DPAPI_AVAILABLE = False


def save_token(path: Path, token: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _DPAPI_AVAILABLE:
        try:
            blob = win32crypt.CryptProtectData(token.encode("utf-8"), "JARVIS session", None, None, None, 0)
            path.write_bytes(blob)
            return
        except Exception:
            logger.warning("Falha ao cifrar o token de sessão via DPAPI; gravando em texto puro.")
    path.write_bytes(token.encode("utf-8"))


def load_token(path: Path) -> str | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if _DPAPI_AVAILABLE:
        try:
            _description, decrypted = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
            return decrypted.decode("utf-8")
        except Exception:
            pass  # pode ter sido gravado em texto puro (DPAPI indisponível na hora) — tenta abaixo

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Token de sessão local ilegível (não é DPAPI nem texto puro) — ignorando.")
        return None


def clear_token(path: Path) -> None:
    path = Path(path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Falha ao remover o arquivo de sessão local (pode já ter sido removido).")
