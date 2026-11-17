"""Shared, atomic login/MFA throttling for the RAUNDO admin.

The in-memory counters that shipped with the first hardening PR solved the
memory-growth issue but did not work across workers.  This module keeps the
same bounded behaviour and adds a Redis backend:

* every failure is written with one atomic MULTI/EXEC pipeline
  (ZREMRANGEBYSCORE + ZADD + ZCARD + PEXPIRE), so concurrent workers see the
  same sliding window;
* limits are keyed by ``(login, ip)`` and by ``ip``.  A stranger failing
  logins for somebody else's account cannot lock that account out from
  another address; the pair counter simply blocks the attacker;
* ``X-Forwarded-For`` is trusted only when the direct peer is a configured
  proxy, and the right-most untrusted address is used;
* identifiers are hashed before they become Redis keys (no raw logins/IPs in
  Redis dumps);
* when Redis is unavailable the demo falls back to process memory.  In
  production ``RATE_LIMIT_BACKEND=redis`` is enforced at startup and the
  limiter fails closed instead of silently losing shared state.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import threading
import time
import uuid
from typing import Protocol

from raundo.config import Settings, get_settings

log = logging.getLogger(__name__)

_KEY_PREFIX = "raundo:admin:rl"
_MAX_MEMORY_KEYS = 4096


class RateLimiterUnavailable(RuntimeError):
    """Redis (or the selected shared backend) cannot serve the request."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


def _key(kind: str, value: str) -> str:
    return f"{_KEY_PREFIX}:{kind}:{_digest(value)}"


class SlidingWindowBackend(Protocol):
    async def hit(self, key: str, limit: int, window_ms: int) -> bool: ...

    async def count(self, key: str, window_ms: int) -> int: ...

    async def clear(self, key: str) -> None: ...


class MemoryBackend:
    """Process-local fallback; bounded and thread-safe."""

    def __init__(self, max_keys: int = _MAX_MEMORY_KEYS) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}
        self._max_keys = max_keys

    def _prune(self, key: str, window_ms: int, now_ms: float) -> list[float]:
        cutoff = now_ms - window_ms
        bucket = [stamp for stamp in self._events.get(key, []) if stamp > cutoff]
        if bucket:
            self._events[key] = bucket
        else:
            self._events.pop(key, None)
        return bucket

    async def hit(self, key: str, limit: int, window_ms: int) -> bool:
        now_ms = time.time() * 1000
        with self._lock:
            bucket = self._prune(key, window_ms, now_ms)
            if len(bucket) >= limit:
                return False
            if key not in self._events and len(self._events) >= self._max_keys:
                self._events.pop(next(iter(self._events)), None)
            bucket.append(now_ms)
            self._events[key] = bucket
            return True

    async def count(self, key: str, window_ms: int) -> int:
        now_ms = time.time() * 1000
        with self._lock:
            return len(self._prune(key, window_ms, now_ms))

    async def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


class RedisBackend:
    """Atomic sliding window on a shared Redis instance."""

    def __init__(self, url: str, *, timeout_seconds: float = 0.5, client=None) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._client = client
        self._down_until = 0.0
        self._lock = threading.Lock()

    def _mark_down(self, exc: Exception) -> None:
        with self._lock:
            self._down_until = time.monotonic() + 15.0
        log.warning("admin limiter: Redis unavailable, backing off: %s", type(exc).__name__)

    def _check_down(self) -> None:
        with self._lock:
            down = time.monotonic() < self._down_until
        if down:
            raise RateLimiterUnavailable("redis_backoff")

    async def _get_client(self):  # noqa: ANN202 - redis client type is optional
        self._check_down()
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(
                self._url,
                decode_responses=True,
                socket_timeout=self._timeout,
                socket_connect_timeout=self._timeout,
                health_check_interval=30,
            )
        return self._client

    async def hit(self, key: str, limit: int, window_ms: int) -> bool:
        client = await self._get_client()
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{uuid.uuid4().hex}"
        try:
            pipe = client.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, now_ms - window_ms)
            pipe.zadd(key, {member: now_ms})
            pipe.zcard(key)
            pipe.pexpire(key, window_ms)
            results = await pipe.execute()
            count = int(results[2])
        except Exception as exc:  # noqa: BLE001 - transport errors are normalized
            self._mark_down(exc)
            raise RateLimiterUnavailable("redis_hit_failed") from exc
        if count > limit:
            try:
                await client.zrem(key, member)
            except Exception as exc:  # noqa: BLE001
                self._mark_down(exc)
                raise RateLimiterUnavailable("redis_cleanup_failed") from exc
            return False
        return True

    async def count(self, key: str, window_ms: int) -> int:
        client = await self._get_client()
        now_ms = int(time.time() * 1000)
        try:
            pipe = client.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, now_ms - window_ms)
            pipe.zcard(key)
            results = await pipe.execute()
            return int(results[1])
        except Exception as exc:  # noqa: BLE001
            self._mark_down(exc)
            raise RateLimiterUnavailable("redis_count_failed") from exc

    async def clear(self, key: str) -> None:
        client = await self._get_client()
        try:
            await client.delete(key)
        except Exception as exc:  # noqa: BLE001
            self._mark_down(exc)
            raise RateLimiterUnavailable("redis_clear_failed") from exc

    async def ping(self) -> bool:
        try:
            client = await self._get_client()
            return bool(await client.ping())
        except Exception:  # noqa: BLE001
            return False


class LoginAttemptLimiter:
    """Policy layer: (login, ip) pair + independent IP budget per surface."""

    def __init__(
        self,
        backend: SlidingWindowBackend,
        *,
        window_seconds: int,
        pair_limit: int,
        ip_limit: int,
        mfa_session_limit: int,
        mfa_ip_limit: int,
        fallback: SlidingWindowBackend | None = None,
        fail_closed: bool = False,
    ) -> None:
        self._backend = backend
        self._fallback = None if fail_closed else fallback
        self._window_ms = max(1, window_seconds) * 1000
        self._pair_limit = max(1, pair_limit)
        self._ip_limit = max(1, ip_limit)
        self._mfa_session_limit = max(1, mfa_session_limit)
        self._mfa_ip_limit = max(1, mfa_ip_limit)

    @property
    def window_seconds(self) -> int:
        return self._window_ms // 1000

    async def _use(self, method: str, *args):  # noqa: ANN002, ANN003, ANN202
        try:
            return await getattr(self._backend, method)(*args)
        except RateLimiterUnavailable:
            if self._fallback is None:
                raise
            log.warning("admin limiter: using process-local fallback", extra={"method": method})
            return await getattr(self._fallback, method)(*args)

    @staticmethod
    def _pair_key(login: str, ip: str) -> str:
        return _key("login-pair", f"{login}\x00{ip}")

    @staticmethod
    def _ip_key(ip: str) -> str:
        return _key("login-ip", ip)

    @staticmethod
    def _mfa_key(session_id: str) -> str:
        return _key("mfa-session", session_id)

    @staticmethod
    def _mfa_ip_key(ip: str) -> str:
        return _key("mfa-ip", ip)

    async def login_blocked(self, *, login: str, ip: str) -> bool:
        pair = await self._use("count", self._pair_key(login, ip), self._window_ms)
        if pair >= self._pair_limit:
            return True
        by_ip = await self._use("count", self._ip_key(ip), self._window_ms)
        return by_ip >= self._ip_limit

    async def record_login_failure(self, *, login: str, ip: str) -> bool:
        pair_ok = await self._use(
            "hit", self._pair_key(login, ip), self._pair_limit, self._window_ms
        )
        ip_ok = await self._use("hit", self._ip_key(ip), self._ip_limit, self._window_ms)
        return bool(pair_ok and ip_ok)

    async def clear_login_failures(self, *, login: str, ip: str) -> None:
        # A successful login proves the pair; keep the IP budget for spraying.
        await self._use("clear", self._pair_key(login, ip))

    async def mfa_blocked(self, *, session_id: str, ip: str) -> bool:
        by_session = await self._use("count", self._mfa_key(session_id), self._window_ms)
        if by_session >= self._mfa_session_limit:
            return True
        by_ip = await self._use("count", self._mfa_ip_key(ip), self._window_ms)
        return by_ip >= self._mfa_ip_limit

    async def record_mfa_failure(self, *, session_id: str, ip: str) -> bool:
        session_ok = await self._use(
            "hit", self._mfa_key(session_id), self._mfa_session_limit, self._window_ms
        )
        ip_ok = await self._use(
            "hit", self._mfa_ip_key(ip), self._mfa_ip_limit, self._window_ms
        )
        return bool(session_ok and ip_ok)

    async def clear_mfa_failures(self, *, session_id: str, ip: str) -> None:
        await self._use("clear", self._mfa_key(session_id))


_limiter: LoginAttemptLimiter | None = None
_limiter_settings_id: int | None = None


def build_login_limiter(settings: Settings | None = None) -> LoginAttemptLimiter:
    settings = settings or get_settings()
    backend_name = (settings.rate_limit_backend or "auto").strip().lower()
    if backend_name not in {"auto", "memory", "redis"}:
        raise RuntimeError(f"Unsupported RATE_LIMIT_BACKEND={backend_name!r}")

    is_production = settings.is_production()
    if backend_name == "memory":
        backend: SlidingWindowBackend = MemoryBackend()
        fallback = None
        fail_closed = False
    else:
        backend = RedisBackend(settings.redis_url or "")
        # Production already refused a memory backend at startup: fail closed.
        fail_closed = is_production and backend_name == "redis"
        fallback = None if fail_closed or backend_name == "memory" else MemoryBackend()

    return LoginAttemptLimiter(
        backend,
        window_seconds=settings.login_fail_window_seconds,
        pair_limit=settings.login_fail_limit,
        ip_limit=settings.login_ip_fail_limit,
        mfa_session_limit=settings.mfa_fail_limit,
        mfa_ip_limit=settings.mfa_ip_fail_limit,
        fallback=fallback,
        fail_closed=fail_closed,
    )


def get_login_limiter(settings: Settings | None = None) -> LoginAttemptLimiter:
    global _limiter, _limiter_settings_id
    settings = settings or get_settings()
    if _limiter is None or _limiter_settings_id != id(settings):
        _limiter = build_login_limiter(settings)
        _limiter_settings_id = id(settings)
    return _limiter


def set_login_limiter(limiter: LoginAttemptLimiter | None) -> None:
    """Test hook: install a limiter for the current settings instance."""
    global _limiter, _limiter_settings_id
    _limiter = limiter
    _limiter_settings_id = id(get_settings()) if limiter is not None else None


# --------------------------------------------------------------- client address
def _networks(value: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets = []
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("trusted proxy entry ignored: invalid network")
    return nets


def _is_trusted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, nets: list) -> bool:
    return any(ip in net for net in nets)


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted_proxies: str) -> str:
    """Return the client address, honouring X-Forwarded-For only behind a proxy.

    Without a matching trusted peer the header is ignored entirely, so a
    direct client cannot spoof its address.  With a trusted proxy the chain is
    read right-to-left and the first untrusted hop wins.
    """
    raw_peer = (peer or "unknown").strip()
    nets = _networks(trusted_proxies)
    if not nets:
        return raw_peer
    try:
        peer_ip = ipaddress.ip_address(raw_peer)
    except ValueError:
        return raw_peer
    if not _is_trusted(peer_ip, nets):
        return raw_peer
    hops: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for chunk in (forwarded_for or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            hops.append(ipaddress.ip_address(chunk))
        except ValueError:
            # Malformed XFF: ignore the header instead of guessing.
            return raw_peer
    for hop in reversed(hops):
        if not _is_trusted(hop, nets):
            return str(hop)
    return str(hops[0]) if hops else raw_peer
