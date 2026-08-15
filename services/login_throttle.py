"""`LoginThrottle` — limitação de tentativas de autenticação por
IDENTIFICADOR, com relógio injetável (v1.5.0).

--------------------------------------------------------------------------
A lacuna que este módulo fecha
--------------------------------------------------------------------------
Desde a v1.0 já existe backoff de força bruta em `UserRepository`
(`failed_login_attempts`/`lockout_until`): ele é **por conta**, mora no banco
e sobrevive a reinício. Ele continua valendo e não foi substituído.

Mas ele só existe para contas que EXISTEM. Uma tentativa contra um
identificador inexistente nunca incrementava contador nenhum — não havia
linha para incrementar. Ou seja: password spraying contra nomes adivinhados
rodava sem limite, e a única defesa era o custo do scrypt. Esta classe é a
camada que falta: conta tentativas por identificador normalizado, exista a
conta ou não.

As duas camadas são complementares e a ordem importa: o throttle é
consultado ANTES de qualquer acesso ao banco, então uma tentativa bloqueada
nem chega a custar um hash.

--------------------------------------------------------------------------
Política (nunca permanente)
--------------------------------------------------------------------------
As primeiras `FREE_ATTEMPTS` falhas não custam nada — errar a senha algumas
vezes é comportamento normal de gente. A partir daí cada falha impõe um
cooldown que dobra, com teto em `MAX_COOLDOWN_SECONDS`. **Nunca existe
bloqueio permanente**: um bloqueio que não expira transforma a proteção numa
negação de serviço contra o próprio dono da conta, que é o resultado oposto
do pretendido. Um acerto zera o estado imediatamente.

--------------------------------------------------------------------------
Persistência: deliberadamente NÃO persiste
--------------------------------------------------------------------------
O estado vive só em RAM, como o `ReauthGuard`. Três razões:

1. O caso que este módulo cobre (identificador inexistente) não tem nada a
   que se ancorar no banco — persistir exigiria uma tabela nova indexada por
   texto arbitrário vindo de quem está atacando, que é ela mesma um vetor de
   crescimento sem limite.
2. O caso que PRECISA sobreviver a reinício (conta real sob ataque) já
   sobrevive: `users.lockout_until` está no banco desde a v1.0.
3. Reiniciar o JARVIS é uma ação local e manual; um atacante que já consegue
   reiniciar o processo do usuário não está limitado por rate limit de login.

`_MAX_TRACKED_IDENTIFIERS` existe para que tentativas contra identificadores
sempre diferentes não façam o dicionário crescer sem fim — as entradas mais
antigas são descartadas quando o teto é atingido.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# Limiares centralizados (v1.5.0, item explícito: configuráveis num lugar só,
# nunca espalhados por chamador).
FREE_ATTEMPTS = 5
BASE_COOLDOWN_SECONDS = 30
MAX_COOLDOWN_SECONDS = 15 * 60
# Uma sequência de falhas "esfria" sozinha depois disto sem nenhuma tentativa
# nova — senão um erro de digitação de ontem contaria contra o login de hoje.
ATTEMPT_WINDOW_SECONDS = 60 * 60
_MAX_TRACKED_IDENTIFIERS = 4096


class AuthChannel(Enum):
    """Canais contados separadamente. Separados de propósito: errar o código
    do autenticador não deve consumir o orçamento de tentativas de senha, e
    vice-versa — são fatores diferentes, com ataques diferentes."""

    PASSWORD = "password"
    TWO_FACTOR = "two_factor"
    RECOVERY_CODE = "recovery_code"


@dataclass
class _Bucket:
    failures: int = 0
    blocked_until: float = 0.0
    last_failure_at: float = field(default=0.0)


class LoginThrottled(Exception):
    """Tentativa recusada antes de qualquer verificação de credencial."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Muitas tentativas. Tente novamente em {retry_after_seconds} segundos."
        )


def normalize_identifier(identifier: str) -> str:
    """Mesma normalização de `user_repository.normalize_username` em espírito
    (minúsculo, sem espaço nas pontas), reimplementada aqui para este módulo
    não depender do repositório — ele é consultado antes de qualquer acesso
    ao banco.

    Normalizar é o que impede `DAVI`, `davi` e ` davi ` de terem três
    orçamentos de tentativa independentes."""
    return (identifier or "").strip().lower()


class LoginThrottle:
    def __init__(
        self,
        *,
        time_source=None,
        free_attempts: int = FREE_ATTEMPTS,
        base_cooldown_seconds: int = BASE_COOLDOWN_SECONDS,
        max_cooldown_seconds: int = MAX_COOLDOWN_SECONDS,
        attempt_window_seconds: int = ATTEMPT_WINDOW_SECONDS,
    ) -> None:
        """`time_source` é injetável para os testes andarem no tempo sem
        `sleep` real — nunca para produção passar um relógio de fora.
        `time.monotonic` (e não o relógio de parede) porque um ajuste de fuso
        ou de horário do sistema não pode encurtar um cooldown."""
        import time

        self._now = time_source or time.monotonic
        self._free_attempts = free_attempts
        self._base_cooldown = base_cooldown_seconds
        self._max_cooldown = max_cooldown_seconds
        self._window = attempt_window_seconds
        self._buckets: dict[tuple[str, AuthChannel], _Bucket] = {}

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def retry_after(self, identifier: str, channel: AuthChannel = AuthChannel.PASSWORD) -> int:
        """Segundos restantes de bloqueio (0 se liberado). Somente leitura."""
        bucket = self._buckets.get((normalize_identifier(identifier), channel))
        if bucket is None:
            return 0
        remaining = bucket.blocked_until - self._now()
        if remaining <= 0:
            return 0
        # Arredonda para cima: nunca reportar "0s" enquanto ainda bloqueia.
        return int(remaining + 0.999)

    def check(self, identifier: str, channel: AuthChannel = AuthChannel.PASSWORD) -> None:
        """Levanta `LoginThrottled` se o identificador está em cooldown.
        Chamado ANTES de qualquer verificação de credencial, para uma tentativa
        bloqueada não custar um hash de senha."""
        remaining = self.retry_after(identifier, channel)
        if remaining > 0:
            raise LoginThrottled(remaining)

    # ------------------------------------------------------------------
    # Registro de resultado
    # ------------------------------------------------------------------

    def register_failure(
        self, identifier: str, channel: AuthChannel = AuthChannel.PASSWORD
    ) -> int:
        """Conta uma falha e devolve os segundos de bloqueio aplicados
        (0 enquanto ainda está dentro das tentativas livres)."""
        key = (normalize_identifier(identifier), channel)
        now = self._now()
        bucket = self._buckets.get(key)

        if bucket is None or (bucket.last_failure_at and now - bucket.last_failure_at > self._window):
            # Sem histórico, ou histórico velho demais: recomeça do zero.
            bucket = _Bucket()
            self._evict_if_needed()
            self._buckets[key] = bucket

        bucket.failures += 1
        bucket.last_failure_at = now

        if bucket.failures <= self._free_attempts:
            return 0

        steps = bucket.failures - self._free_attempts - 1
        seconds = min(self._base_cooldown * (2**steps), self._max_cooldown)
        bucket.blocked_until = now + seconds
        # Nunca logamos o identificador: ele pode ser o e-mail de alguém, e
        # log de tentativa falha é exatamente onde não se quer dado pessoal.
        logger.warning(
            "Autenticação (%s) em cooldown de %ss após %s falhas consecutivas.",
            channel.value,
            seconds,
            bucket.failures,
        )
        return seconds

    def register_success(
        self, identifier: str, channel: AuthChannel = AuthChannel.PASSWORD
    ) -> None:
        """Sucesso zera o estado — quem provou ser o dono não deve carregar o
        peso das tentativas erradas de ninguém."""
        self._buckets.pop((normalize_identifier(identifier), channel), None)

    def reset(self) -> None:
        self._buckets.clear()

    def _evict_if_needed(self) -> None:
        if len(self._buckets) < _MAX_TRACKED_IDENTIFIERS:
            return
        # Descarta primeiro o que já não bloqueia nada; se tudo estiver ativo,
        # descarta o mais antigo. Nunca deixa o dicionário crescer sem limite.
        now = self._now()
        expired = [key for key, bucket in self._buckets.items() if bucket.blocked_until <= now]
        for key in expired:
            del self._buckets[key]
        if len(self._buckets) >= _MAX_TRACKED_IDENTIFIERS:
            oldest = min(self._buckets, key=lambda key: self._buckets[key].last_failure_at)
            del self._buckets[oldest]
