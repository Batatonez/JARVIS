"""TOTP (RFC 6238) e HOTP (RFC 4226) com a biblioteca padrão.

Implementado sobre `hmac`/`hashlib`/`struct`, sem dependência externa. Isso
**não** contraria a regra de "não inventar algoritmo": o algoritmo é o do
RFC, seguido à risca — o que não fazemos é criar um esquema próprio. Os
parâmetros são os do padrão de fato usado por Google Authenticator, Authy,
1Password, Microsoft Authenticator e afins:

    SHA-1, 6 dígitos, janela de 30 segundos

(SHA-1 aqui é o do HMAC, não hash de senha: o RFC 6238 e todos os
autenticadores populares usam HMAC-SHA1, e HMAC-SHA1 não é afetado pelas
colisões que aposentaram SHA-1 para assinatura.)

Tolerância de relógio: `verify()` aceita o passo atual e um passo para cada
lado (±30s), o mínimo recomendado pelo RFC 6238 §5.2 para lidar com drift.
Aceitar mais janelas ampliaria a superfície de brute-force sem ganho real.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

# Parâmetros padrão dos autenticadores. Mudá-los quebraria a compatibilidade
# com os apps que o usuário já tem instalado.
DIGITS = 6
PERIOD_SECONDS = 30
ALGORITHM = "SHA1"
# 160 bits — o tamanho recomendado pelo RFC 4226 §4 para chaves HMAC-SHA1.
_SECRET_BYTES = 20
# Uma janela para trás e uma para frente (RFC 6238 §5.2).
_DEFAULT_WINDOW = 1


def generate_secret() -> str:
    """Segredo novo em Base32 sem padding — o formato que os autenticadores
    esperam quando o usuário digita a chave à mão."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def normalize_secret(secret: str) -> str:
    """Aceita o segredo como o usuário digitaria (minúsculas, espaços,
    padding faltando) e devolve Base32 canônico."""
    cleaned = secret.strip().replace(" ", "").replace("-", "").upper()
    padding = (-len(cleaned)) % 8
    return cleaned + ("=" * padding)


def _decode_secret(secret: str) -> bytes:
    try:
        return base64.b32decode(normalize_secret(secret), casefold=True)
    except Exception as exc:  # binascii.Error e afins
        raise ValueError("Segredo TOTP inválido.") from exc


def hotp(secret: str, counter: int, *, digits: int = DIGITS) -> str:
    """RFC 4226: HMAC-SHA1 do contador, truncamento dinâmico, módulo 10^d."""
    key = _decode_secret(secret)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def totp(secret: str, *, at: float | None = None, period: int = PERIOD_SECONDS) -> str:
    """RFC 6238: HOTP com contador = tempo Unix dividido pelo período."""
    moment = time.time() if at is None else at
    return hotp(secret, int(moment // period))


def verify(
    secret: str,
    code: str,
    *,
    at: float | None = None,
    period: int = PERIOD_SECONDS,
    window: int = _DEFAULT_WINDOW,
) -> bool:
    """Compara em tempo constante contra as janelas aceitas.

    `at` existe para os testes usarem tempo mockado (item 68) — nunca para
    produção passar um relógio de fora."""
    candidate = (code or "").strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != DIGITS:
        return False

    moment = time.time() if at is None else at
    counter = int(moment // period)
    matched = False
    for step in range(-window, window + 1):
        # Sem short-circuit de propósito: sair do laço no primeiro acerto
        # vazaria por timing qual janela bateu.
        matched |= hmac.compare_digest(hotp(secret, counter + step), candidate)
    return matched


def provisioning_uri(*, secret: str, account_name: str, issuer: str) -> str:
    """URI `otpauth://` que vira o QR Code lido pelo autenticador.

    Formato definido pela especificação Key Uri do Google Authenticator, que
    é o que todos os apps implementam."""
    label = quote(f"{issuer}:{account_name}", safe="")
    params = (
        f"secret={quote(normalize_secret(secret).rstrip('='), safe='')}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm={ALGORITHM}"
        f"&digits={DIGITS}"
        f"&period={PERIOD_SECONDS}"
    )
    return f"otpauth://totp/{label}?{params}"


def qr_matrix(data: str) -> list[list[bool]]:
    """Matriz booleana do QR Code (`True` = módulo escuro).

    Devolvemos a MATRIZ, não uma imagem: o HUD desenha os quadrados em QML
    com um `Repeater`, o que evita depender de PIL/QtSvg e evita gravar o
    segredo num arquivo de imagem temporário em disco.

    Lista vazia se o pacote `qrcode` não estiver instalado — o HUD sempre
    mostra também a chave textual, então a ausência do QR degrada a
    experiência sem bloquear a ativação do 2FA (item 47)."""
    try:
        import qrcode
    except ImportError:
        return []
    code = qrcode.QRCode(border=1)
    code.add_data(data)
    code.make(fit=True)
    return [[bool(cell) for cell in row] for row in code.get_matrix()]
