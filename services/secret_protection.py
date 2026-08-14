"""Proteção em repouso de segredos que precisam ser LIDOS de volta (v1.3).

Diferente de senha: uma senha só precisa ser verificada, então guardamos um
hash irreversível (`services/password_hashing.py`). O segredo TOTP precisa
ser recuperado a cada login para recalcular o código, então hash não serve —
ele precisa ser cifrado.

Mecanismo: **DPAPI do Windows** (`CryptProtectData`/`CryptUnprotectData`, via
`win32crypt` do `pywin32`), o mesmo já usado pelo token de sessão em
`services/session_store.py`. A chave é derivada da conta do Windows do
usuário e gerenciada pelo sistema operacional — o JARVIS não inventa nem
guarda chave nenhuma, que é exatamente o que o item 48 da v1.3 pede.

**Degradação explícita.** Sem DPAPI (não-Windows, ou `pywin32` ausente) o
valor é gravado com um marcador `plain:` em vez de silenciosamente parecer
cifrado. O chamador consegue perguntar `is_protected()` e o HUD/auditoria
consegue dizer a verdade sobre o que está acontecendo. Um formato
auto-descritivo também permite migrar de esquema depois sem invalidar o que
já está no banco:

    dpapi:<base64>     cifrado pela DPAPI do usuário do Windows
    plain:<base64>     sem cifra disponível no ambiente
"""

import base64
import logging

logger = logging.getLogger(__name__)

_DESCRIPTION = "JARVIS 2FA secret"

try:
    import win32crypt

    _DPAPI_AVAILABLE = True
except ImportError:
    _DPAPI_AVAILABLE = False


def is_protection_available() -> bool:
    """`True` quando existe cifra real de verdade neste ambiente."""
    return _DPAPI_AVAILABLE


def protect(secret: str) -> str:
    """Cifra um segredo para guardar no banco. Nunca loga o valor — nem em
    DEBUG, nem no caminho de erro."""
    if not secret:
        raise ValueError("Segredo vazio não pode ser protegido.")
    raw = secret.encode("utf-8")
    if _DPAPI_AVAILABLE:
        try:
            blob = win32crypt.CryptProtectData(raw, _DESCRIPTION, None, None, None, 0)
            return "dpapi:" + base64.b64encode(blob).decode("ascii")
        except Exception:
            logger.warning(
                "Falha ao cifrar segredo via DPAPI; gravando sem cifra (marcado como 'plain')."
            )
    return "plain:" + base64.b64encode(raw).decode("ascii")


def unprotect(stored: str) -> str | None:
    """Recupera o segredo. `None` se o valor for ilegível — nunca levanta
    exceção com o conteúdo dentro, que é como um segredo acaba num traceback
    e daí num log."""
    if not stored:
        return None
    try:
        scheme, _, payload = stored.partition(":")
        raw = base64.b64decode(payload.encode("ascii"))
    except Exception:
        logger.warning("Segredo armazenado em formato irreconhecível — ignorando.")
        return None

    if scheme == "dpapi":
        if not _DPAPI_AVAILABLE:
            logger.warning("Segredo cifrado com DPAPI, mas o DPAPI não está disponível aqui.")
            return None
        try:
            _description, decrypted = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
            return decrypted.decode("utf-8")
        except Exception:
            logger.warning("Não foi possível decifrar o segredo com a DPAPI deste usuário.")
            return None

    if scheme == "plain":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    logger.warning("Esquema de proteção desconhecido em segredo armazenado — ignorando.")
    return None


def is_protected(stored: str | None) -> bool:
    """`True` só quando o valor está de fato cifrado (auditoria/diagnóstico)."""
    return bool(stored) and stored.startswith("dpapi:")
