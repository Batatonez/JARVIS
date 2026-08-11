"""Hash de senha para contas locais — `hashlib.scrypt` (biblioteca padrão do
Python, disponível desde 3.6, ligada ao OpenSSL). Scrypt é um dos KDFs
recomendados pelo OWASP Password Storage Cheat Sheet (junto com Argon2id) —
memory-hard, resistente a ataque por GPU/ASIC, com salt e custo ajustáveis.
Escolhido em vez de `bcrypt`/`argon2-cffi` para não somar uma dependência
externa nova só para isto: `scrypt` já vem no Python, sem nenhum pacote a
mais no `requirements.txt`.

Nunca implementa hashing/criptografia própria — só chama a stdlib
corretamente (salt aleatório de 16 bytes via `secrets`, comparação em tempo
constante via `hmac.compare_digest`). Nunca loga a senha nem o hash em texto
livre fora do valor já hasheado que é, por definição, o que persistimos.

Formato armazenado (uma string só, auto-descritiva — permite subir os
parâmetros de custo no futuro sem invalidar hashes antigos):

    scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>
"""

import hashlib
import hmac
import secrets

# N=2**15 (32768), r=8, p=1: mais forte que o mínimo interativo recomendado
# pela documentação do Python (N=2**14), ainda rápido o suficiente para um
# login de desktop (~150-300ms nesta máquina) — não é um serviço público de
# alto throughput, então podemos pagar um pouco mais de custo por hash.
_N = 2**15
_R = 8
_P = 1
_SALT_BYTES = 16
_DKLEN = 32
# scrypt precisa de ~128*N*r*p bytes de memória (aqui, exatamente os 32 MiB
# que são o default do OpenSSL) — sem folga, `hashlib.scrypt` recusa com
# "memory limit exceeded". Pedimos um teto explícito maior (documentado pela
# própria stdlib: "for many cases the default must be increased").
_MAXMEM = 64 * 1024 * 1024


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Senha não pode ser vazia.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Nunca levanta exceção para hash malformado — trata como não-confere,
    já que o chamador só precisa saber se a senha bate ou não."""
    try:
        algorithm, n_str, r_str, p_str, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_str), int(r_str), int(p_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=_MAXMEM
    )
    return hmac.compare_digest(candidate, expected)
