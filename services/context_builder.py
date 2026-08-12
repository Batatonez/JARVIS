"""Construção e sanitização do contexto enviado a um provider de IA externo.

Antes da v1.0 o único destino do contexto era o Claude Agent SDK rodando
localmente. Agora o texto pode sair da máquina (OpenRouter), então existe um
ponto único e auditável que decide **o que sai** — e o corta.

Duas responsabilidades, deliberadamente separadas:

1. **Data minimization** (`build_system_prompt`): só identidade de runtime +
   memória controlada (perfil/preferências), truncada a um teto de
   caracteres. Nunca o banco, nunca a conversa de outro usuário, nunca
   metadados internos.
2. **Sanitização** (`sanitize_context`): rede de segurança contra segredo
   que tenha vazado *para dentro* da memória por engano — o usuário escreve
   `memory/profile.md` à mão, e nada impede que ele cole uma chave de API
   lá. Padrões conhecidos (chaves de provider, `Authorization: Bearer`,
   hashes de senha no nosso formato, tokens longos) viram `[REDACTED]`.

Isto é defesa em profundidade, não a defesa principal: o JARVIS nunca
*coloca* segredo no contexto (senha, hash, token de sessão e código de
verificação vivem só no banco/RAM e não passam por aqui). Ver
docs/security.md, seção "Privacidade ao chamar IA".
"""

import re

_REDACTED = "[REDACTED]"

# Padrões de segredo conhecidos. Ordem importa: o mais específico primeiro.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Chaves de provider (OpenRouter/OpenAI `sk-...`, Anthropic `sk-ant-...`).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), _REDACTED),
    # Cabeçalho de autorização colado por engano.
    (re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"), f"Authorization: Bearer {_REDACTED}"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"), f"Bearer {_REDACTED}"),
    # Hash de senha no nosso formato (services/password_hashing.py).
    (re.compile(r"\bscrypt\$\d+\$\d+\$\d+\$[0-9a-f]+\$[0-9a-f]+"), _REDACTED),
    # Atribuição explícita de variável de segredo (`OPENROUTER_API_KEY=...`).
    (
        re.compile(r"(?i)\b([A-Z_]*(?:API_KEY|SECRET|TOKEN|PASSWORD))\s*[=:]\s*\S+"),
        lambda m: f"{m.group(1)}={_REDACTED}",
    ),
    # Blobs hex longos (hash de token de sessão tem 64 chars).
    (re.compile(r"\b[0-9a-f]{48,}\b"), _REDACTED),
)


def sanitize_context(text: str) -> str:
    """Redige segredos conhecidos. Nunca levanta exceção: sanitizar é uma
    rede de segurança, e falhar aqui não pode derrubar uma conversa."""
    if not text:
        return ""
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def truncate_context(text: str, max_chars: int) -> str:
    """Corta no limite, avisando no próprio texto que houve corte — a IA não
    deve concluir que a memória do usuário simplesmente acaba ali."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[... memória truncada por limite de contexto ...]"


def prepare_memory_context(text: str, *, max_chars: int) -> str:
    """Sanitiza e trunca, nessa ordem — o único preparo que a memória sofre
    antes de sair da máquina. Chamado por `JarvisCore.build_memory_context()`,
    de modo que TODO provider (Claude Agent SDK ou ProviderRouter) recebe a
    mesma memória já tratada; nenhum provider precisa lembrar de sanitizar.

    A composição com a identidade de runtime continua em
    `services/runtime_identity.py::build_system_prompt` — não duplicamos
    aquilo aqui."""
    return truncate_context(sanitize_context(text or ""), max_chars)
