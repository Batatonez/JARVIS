"""O que o JARVIS NUNCA indexa (v1.8).

--------------------------------------------------------------------------
Por que isto é um módulo, e não `if` espalhado
--------------------------------------------------------------------------
Uma regra de exclusão escrita em três lugares vira três regras diferentes na
primeira vez que alguém edita uma delas — e a que ficar para trás é a que vai
indexar um `.env`. Aqui existe uma lista, uma função, e todo mundo pergunta
para ela.

--------------------------------------------------------------------------
As três categorias, e por que cada uma existe
--------------------------------------------------------------------------
**Segredo.** `.env`, chave SSH, store de credencial, perfil de navegador.
Indexar significa copiar trechos para um banco pesquisável e potencialmente
mostrar num resultado de busca. Uma senha não deve ser encontrável por
"procura senha".

**Ruído.** `node_modules`, `.venv`, `__pycache__`, `.git`. São dezenas de
milhares de arquivos que ninguém procura pelo nome, e que fariam o índice
levar minutos e ocupar espaço para piorar todo resultado de busca.

**Estado do próprio JARVIS.** `data/jarvis.db`, `.claude`, `ruvector.db`. O
assistente indexar o próprio banco de contas é obviamente errado, e é o tipo
de coisa que só se percebe depois de acontecer.

--------------------------------------------------------------------------
Regra que não se afrouxa
--------------------------------------------------------------------------
Mesmo quando o usuário adiciona uma pasta explicitamente, estas exclusões
continuam valendo. Adicionar `C:\\Projetos` é dizer "procure meus projetos
aqui", não "copie meus segredos para um índice". Se um dia isso precisar
mudar, tem que ser uma escolha consciente e separada — nunca um efeito
colateral de escolher uma pasta.
"""

import unicodedata
from pathlib import Path

# Diretórios inteiros que nunca são percorridos. Comparados pelo nome exato
# da pasta, em minúsculas.
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        # Ferramentas de desenvolvimento — volume enorme, valor de busca zero
        "node_modules", ".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".tox", ".gradle", "target", "vendor",
        ".venv", "venv", "env", ".env.d", "site-packages", "dist-info",
        # Estado de agentes/ferramentas deste projeto
        ".claude", ".claude-flow", ".swarm", ".cursor", ".idea", ".vscode",
        # Segredos e credenciais
        ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker",
        "credentials", "secrets", "keystore", ".password-store",
        # Perfis de navegador: guardam cookies e senhas salvas
        "user data", "profiles", "chrome", "firefox", "edge",
        # Sistema — nada aqui é documento do usuário
        "windows", "system32", "syswow64", "program files", "program files (x86)",
        "$recycle.bin", "system volume information", "winsxs",
        # Caches e temporários
        "cache", "caches", ".cache", "temp", "tmp", "logs",
    }
)

# Arquivos nunca indexados, por nome exato.
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".env", ".env.local", ".env.production", ".env.development", ".env.test",
        "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
        ".netrc", "_netrc", ".pgpass", ".htpasswd",
        "credentials.json", "client_secret.json", "service-account.json",
        "ruvector.db", "jarvis.db", "session.local",
        ".git-credentials", ".npmrc", ".pypirc",
    }
)

# Prefixos e sufixos de nome que denunciam segredo ou estado de runtime.
EXCLUDED_FILE_PREFIXES = (".env.", "id_rsa", "id_ed25519")
EXCLUDED_FILE_SUFFIXES = (
    ".pem", ".key", ".pfx", ".p12", ".keystore", ".jks",  # material criptográfico
    ".db-wal", ".db-shm",                                   # journal do SQLite
)

# Extensões cujo CONTEÚDO nunca é lido como texto. Tentar `read_text()` num
# executável não é só inútil: lê megabytes de binário para procurar palavra
# que não existe.
BINARY_EXTENSIONS = frozenset(
    {
        ".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".sys", ".pyd", ".o", ".obj",
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".cab",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".psd",
        ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac",
        ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm", ".flv",
        ".ttf", ".otf", ".woff", ".woff2", ".eot",
        ".pyc", ".class", ".jar", ".wasm", ".onnx", ".pt", ".bin", ".safetensors",
        ".sqlite", ".sqlite3", ".db", ".mdb", ".accdb",
    }
)

# Extensões cujo conteúdo é texto e vale indexar. Lista explícita em vez de
# "tudo que não é binário": um `.dat` desconhecido pode ser qualquer coisa, e
# a lista fechada é o que mantém o índice previsível.
TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".log",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
        ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".sh", ".ps1", ".bat",
        ".html", ".htm", ".css", ".scss", ".xml", ".yaml", ".yml", ".toml", ".ini",
        ".cfg", ".conf", ".json", ".csv", ".tsv", ".sql", ".qml",
    }
)

# Documentos com extrator próprio (ver `content_extractor.py`).
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx"})


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.lower().strip()


def is_excluded_directory(path: Path) -> bool:
    """A pasta inteira deve ser pulada?

    Checa também se QUALQUER ancestral está excluído: sem isso, adicionar
    `C:\\Projetos\\app\\node_modules\\pacote` como raiz driblaria a regra."""
    name = _normalize(path.name)
    if name in EXCLUDED_DIRECTORY_NAMES:
        return True
    # Pasta oculta (`.algo`) que não esteja explicitamente liberada: quase
    # sempre é estado de ferramenta, não documento.
    if name.startswith(".") and name not in (".",):
        return True
    return False


def is_excluded_file(path: Path) -> bool:
    name = _normalize(path.name)
    if name in EXCLUDED_FILE_NAMES:
        return True
    if name.startswith(EXCLUDED_FILE_PREFIXES):
        return True
    if name.endswith(EXCLUDED_FILE_SUFFIXES):
        return True
    # Arquivo oculto: mesmo raciocínio das pastas.
    if name.startswith("."):
        return True
    return False


def is_binary_extension(extension: str) -> bool:
    return (extension or "").lower() in BINARY_EXTENSIONS


def is_text_extension(extension: str) -> bool:
    return (extension or "").lower() in TEXT_EXTENSIONS


def is_document_extension(extension: str) -> bool:
    return (extension or "").lower() in DOCUMENT_EXTENSIONS


def can_extract_content(extension: str) -> bool:
    """O conteúdo deste arquivo pode ser lido para indexação/resumo?"""
    return is_text_extension(extension) or is_document_extension(extension)


def path_contains_excluded_segment(path: Path) -> bool:
    """Algum diretório do caminho está excluído?

    Usado na validação de uma ação sobre arquivo (abrir, resumir): mesmo que
    um caminho chegue por outro meio, ele não pode apontar para dentro de uma
    árvore que o índice recusou."""
    for part in path.parts:
        if _normalize(part) in EXCLUDED_DIRECTORY_NAMES:
            return True
    return False
