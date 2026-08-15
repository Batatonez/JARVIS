"""Política de senha — fonte ÚNICA da regra "esta senha pode ser usada?"
(v1.5.0).

Até a v1.3 a regra vivia em `account_service.validate_password` e era só um
comprimento mínimo de 8. Ela continua sendo chamada do mesmo lugar (nada no
resto do sistema precisou saber que a política mudou de casa), mas o conteúdo
da política passou a morar aqui, porque agora ela tem três partes que se
justificam separadamente:

1. **Comprimento antes de composição.** O piso subiu para 12 caracteres em
   senhas NOVAS. Não exigimos "1 maiúscula, 1 número, 1 símbolo": regra de
   composição empurra o usuário para `Senha1!` — previsível, curta e presente
   em qualquer wordlist. Comprimento é o único fator que aumenta o espaço de
   busca de forma monotônica.
2. **Resistência a senha comum**, verificada **localmente** contra a lista
   embutida em `COMMON_PASSWORDS`. Nunca por API externa: consultar um
   serviço remoto para validar senha significaria enviar a senha (ou um
   prefixo dela) para fora da máquina do usuário, e este projeto não faz
   isso em nenhuma circunstância.
3. **Não derivar dos próprios dados da conta.** `davi@exemplo.com` como senha
   de `davi@exemplo.com` é trivialmente adivinhável por quem já sabe quem é o
   dono — que é exatamente o atacante realista de um assistente pessoal.

O que a política **permite** de propósito: espaço, passphrase, acento e
qualquer caractere Unicode. `hashlib.scrypt` recebe `password.encode("utf-8")`
(ver `services/password_hashing.py`), que não trunca em NUL nem em 72 bytes
como o bcrypt faria — então não há truncamento silencioso a esconder, e
restringir o charset só reduziria entropia.

`MAX_LENGTH` existe por um motivo diferente de "regra de senha": scrypt custa
memória e CPU proporcionalmente ao tamanho da entrada, então uma senha de
vários megabytes seria um caminho de negação de serviço contra o próprio
processo. O teto é alto o bastante para nenhuma passphrase real esbarrar
nele.

**Compatibilidade (v1.5.0, item explícito)**: a política é aplicada a senhas
NOVAS — cadastro e troca. Nenhum hash existente é invalidado e nenhuma conta
antiga é bloqueada por ter uma senha de 8 caracteres; ela continua entrando
normalmente. Subir o piso para quem já entrou seria transformar uma melhoria
de segurança numa perda de acesso.
"""

import unicodedata
from dataclasses import dataclass
from enum import Enum

# Piso de senhas NOVAS. 12 é o mínimo recomendado pelo OWASP Authentication
# Cheat Sheet quando não há um segundo fator obrigatório.
MIN_LENGTH = 12
# Teto anti-abuso do hasher (ver docstring). Não é uma regra de força.
MAX_LENGTH = 256
# Abaixo disto, uma "parte do usuário" (ex.: um username de 2 letras) casaria
# com senha demais para a checagem significar alguma coisa.
_MIN_DERIVATION_FRAGMENT = 4


class PasswordStrength(Enum):
    """Rótulo exibido no HUD. Deliberadamente três níveis: um medidor com
    barra contínua sugere uma precisão que nenhuma heurística local tem."""

    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


class InvalidPasswordError(ValueError):
    """Senha recusada pela política. A mensagem é apresentável ao usuário e
    **nunca** ecoa a senha."""


# Lista local e curta de propósito: cobre o que aparece no topo de qualquer
# vazamento público (incluindo as variantes em português, que uma lista
# importada em inglês não pegaria) sem virar um arquivo de milhões de linhas
# carregado em RAM a cada validação. Comparação sempre em minúsculas.
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "123456", "1234567", "12345678", "123456789", "1234567890",
        "12345678910", "123123123", "111111111", "abcdefghijkl",
        "password", "password1", "password123", "passw0rd", "senha123",
        "senha1234", "minhasenha", "senhasenha", "senhasegura",
        "qwertyuiop", "qwerty123456", "asdfghjkl", "zxcvbnm123",
        "iloveyou123", "admin123456", "administrador", "administrator",
        "welcome123", "letmein123", "changeme123", "trustno1234",
        "brasil123", "flamengo123", "corinthians", "palmeiras123",
        "jarvis123", "jarvisjarvis", "jarvis12345",
    }
)


@dataclass(frozen=True)
class PasswordRequirement:
    """Um item da lista de checagens exibida no HUD."""

    key: str
    label: str
    satisfied: bool


@dataclass(frozen=True)
class PasswordAssessment:
    """Resultado completo da avaliação — o que a UI precisa para desenhar o
    indicador, e o que o backend usa para aceitar ou recusar.

    Nenhum campo aqui carrega a senha: o HUD nunca precisa dela de volta, e
    devolvê-la só criaria mais um lugar por onde ela poderia vazar para log."""

    acceptable: bool
    strength: PasswordStrength
    requirements: tuple[PasswordRequirement, ...]
    message: str = ""


def _normalize(value: str) -> str:
    """Casefold + NFKD para a comparação de "senha derivada dos dados da
    conta" enxergar `Dávi` e `davi` como a mesma coisa."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.casefold().strip()


def _account_fragments(*, username: str = "", email: str = "", display_name: str = "") -> set[str]:
    """Pedaços dos dados da conta que uma senha não deve conter/ser.

    O e-mail entra inteiro E só a parte local: `davi@exemplo.com` e `davi`
    são igualmente ruins como senha para essa conta."""
    fragments: set[str] = set()
    for raw in (username, display_name, email):
        normalized = _normalize(raw)
        if len(normalized) >= _MIN_DERIVATION_FRAGMENT:
            fragments.add(normalized)
    local_part = _normalize(email).split("@", 1)[0]
    if len(local_part) >= _MIN_DERIVATION_FRAGMENT:
        fragments.add(local_part)
    # Palavras do nome de exibição ("Davi Cunzolo" -> "davi", "cunzolo").
    for word in _normalize(display_name).split():
        if len(word) >= _MIN_DERIVATION_FRAGMENT:
            fragments.add(word)
    return fragments


def is_common_password(password: str) -> bool:
    """Casa a senha contra a lista de três formas, não só por igualdade.

    Igualdade sozinha era fraca demais — descoberto por um teste desta versão:
    `password123456` passava como "não comum" (e portanto MÉDIA) só porque a
    lista tem `password123` e não a variante com mais dígitos. Acrescentar
    cada sufixo possível à lista seria um jogo perdido; o que resolve é
    reconhecer que uma senha comum com dígitos colados no fim continua sendo
    a mesma senha comum."""
    normalized = _normalize(password)
    if normalized in COMMON_PASSWORDS:
        return True
    # `senha1234!` -> `senha` : dígitos e pontuação no fim não acrescentam
    # imprevisibilidade real a uma base já conhecida.
    trimmed = normalized.rstrip("0123456789!@#$%&*_-.")
    if trimmed and trimmed in COMMON_PASSWORDS:
        return True
    # `password123456` começa com `password123`. O piso de 8 evita que uma
    # entrada curta da lista transforme qualquer senha longa em "comum".
    return any(
        len(common) >= 8 and normalized.startswith(common) for common in COMMON_PASSWORDS
    )


def derives_from_account(
    password: str, *, username: str = "", email: str = "", display_name: str = ""
) -> bool:
    """`True` se a senha CONTÉM (não só "é igual a") um dado da conta.

    Contém, e não igual: `davi2024!!` é tão adivinhável quanto `davi` para
    quem conhece o dono da conta."""
    normalized = _normalize(password)
    if not normalized:
        return False
    return any(
        fragment in normalized
        for fragment in _account_fragments(username=username, email=email, display_name=display_name)
    )


def _character_classes(password: str) -> int:
    classes = 0
    if any(ch.islower() for ch in password):
        classes += 1
    if any(ch.isupper() for ch in password):
        classes += 1
    if any(ch.isdigit() for ch in password):
        classes += 1
    if any(not ch.isalnum() for ch in password):
        classes += 1
    return classes


def strength_of(password: str) -> PasswordStrength:
    """Heurística simples e honesta: comprimento manda, variedade ajuda.

    Senha comum é SEMPRE fraca, por mais longa que seja — `password123456`
    tem 14 caracteres e zero resistência."""
    if is_common_password(password):
        return PasswordStrength.WEAK
    length = len(password)
    if length >= 20:
        return PasswordStrength.STRONG
    if length < MIN_LENGTH:
        return PasswordStrength.WEAK
    classes = _character_classes(password)
    if length >= 16 or classes >= 3:
        return PasswordStrength.STRONG
    return PasswordStrength.MEDIUM


def assess(
    password: str, *, username: str = "", email: str = "", display_name: str = ""
) -> PasswordAssessment:
    """Avaliação completa, usada tanto pelo indicador visual (que chama a cada
    tecla) quanto pela validação de verdade (que chama no submit). Um único
    caminho para os dois evita a divergência clássica de "a UI diz que está
    ok e o backend recusa"."""
    password = password or ""
    long_enough = MIN_LENGTH <= len(password) <= MAX_LENGTH
    not_common = bool(password) and not is_common_password(password)
    not_derived = bool(password) and not derives_from_account(
        password, username=username, email=email, display_name=display_name
    )

    requirements = (
        PasswordRequirement("length", f"Pelo menos {MIN_LENGTH} caracteres", long_enough),
        PasswordRequirement("uncommon", "Não é uma senha comum", not_common),
        PasswordRequirement("not_personal", "Não usa seu nome, username ou e-mail", not_derived),
    )
    acceptable = all(requirement.satisfied for requirement in requirements)

    message = ""
    if not password:
        message = "Informe uma senha."
    elif len(password) < MIN_LENGTH:
        message = f"A senha precisa ter pelo menos {MIN_LENGTH} caracteres."
    elif len(password) > MAX_LENGTH:
        message = f"A senha pode ter no máximo {MAX_LENGTH} caracteres."
    elif not not_common:
        message = "Essa senha é muito comum. Escolha uma que não apareça em listas de senhas vazadas."
    elif not not_derived:
        message = "A senha não pode conter seu nome, username ou e-mail."

    return PasswordAssessment(
        acceptable=acceptable,
        strength=strength_of(password),
        requirements=requirements,
        message=message,
    )


def validate_password(
    password: str, *, username: str = "", email: str = "", display_name: str = ""
) -> str:
    """Aplica a política e devolve a senha intacta, ou levanta
    `InvalidPasswordError` com uma mensagem já apresentável.

    Devolve a senha **sem nenhuma transformação**: normalizar, cortar espaço
    nas pontas ou truncar mudaria silenciosamente o que o usuário escolheu, e
    ele nunca mais conseguiria entrar digitando o que digitou."""
    assessment = assess(password, username=username, email=email, display_name=display_name)
    if not assessment.acceptable:
        raise InvalidPasswordError(assessment.message or "Senha fora da política.")
    return password
