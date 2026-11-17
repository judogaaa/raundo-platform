"""Shared login/MFA limiter and production config guards."""

from __future__ import annotations

import os

import pytest

from raundo.admin_api import login_limiter as limiter_module
from raundo.admin_api.login_limiter import (
    LoginAttemptLimiter,
    MemoryBackend,
    RedisBackend,
    resolve_client_ip,
)
from raundo.config import Settings


class FakeRedis:
    """Minimal async Redis surface used by RedisBackend (multi-worker tests)."""

    def __init__(self) -> None:
        self.scores: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True):  # noqa: ANN201, ARG002
        return _FakePipeline(self)

    async def zrem(self, key: str, member: str) -> int:
        return 1 if self.scores.get(key, {}).pop(member, None) is not None else 0

    async def delete(self, key: str) -> int:
        return 1 if self.scores.pop(key, None) is not None else 0

    async def ping(self) -> bool:
        return True


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key: str, minimum: float, maximum: float):  # noqa: ANN201
        self._ops.append(("prune", key, minimum, maximum))
        return self

    def zadd(self, key: str, mapping: dict[str, float]):  # noqa: ANN201
        self._ops.append(("add", key, mapping))
        return self

    def zcard(self, key: str):  # noqa: ANN201
        self._ops.append(("card", key))
        return self

    def pexpire(self, key: str, milliseconds: int):  # noqa: ANN201, ARG002
        self._ops.append(("expire", key, milliseconds))
        return self

    async def execute(self) -> list:
        results: list = []
        for op in self._ops:
            if op[0] == "prune":
                _, key, minimum, maximum = op
                bucket = self._redis.scores.get(key, {})
                for member, score in list(bucket.items()):
                    if minimum < score <= maximum:
                        bucket.pop(member, None)
                results.append(None)
            elif op[0] == "add":
                _, key, mapping = op
                self._redis.scores.setdefault(key, {}).update(mapping)
                results.append(1)
            elif op[0] == "card":
                _, key = op
                results.append(len(self._redis.scores.get(key, {})))
            else:
                results.append(True)
        return results


def _policy(backend, **overrides) -> LoginAttemptLimiter:  # noqa: ANN001
    values = {
        "window_seconds": 600,
        "pair_limit": 3,
        "ip_limit": 10,
        "mfa_session_limit": 3,
        "mfa_ip_limit": 10,
    }
    values.update(overrides)
    return LoginAttemptLimiter(backend, **values)


@pytest.mark.asyncio
async def test_pair_limit_does_not_lock_the_account_for_other_ips() -> None:
    limiter = _policy(MemoryBackend(), pair_limit=2, ip_limit=50)
    attacker_ip = "203.0.113.9"
    victim_ip = "198.51.100.7"
    for _ in range(2):
        assert await limiter.record_login_failure(login="victim", ip=attacker_ip)
    assert await limiter.login_blocked(login="victim", ip=attacker_ip)
    # A stranger cannot lock the real owner out from a different address.
    assert not await limiter.login_blocked(login="victim", ip=victim_ip)
    # And the successful login clears that pair budget.
    await limiter.clear_login_failures(login="victim", ip=victim_ip)
    assert not await limiter.login_blocked(login="victim", ip=victim_ip)


@pytest.mark.asyncio
async def test_ip_budget_blocks_password_spraying_across_logins() -> None:
    limiter = _policy(MemoryBackend(), pair_limit=20, ip_limit=2)
    ip = "203.0.113.10"
    assert await limiter.record_login_failure(login="one", ip=ip)
    assert await limiter.record_login_failure(login="two", ip=ip)
    assert not await limiter.record_login_failure(login="three", ip=ip)
    assert await limiter.login_blocked(login="three", ip=ip)
    # Another address is unaffected.
    assert not await limiter.login_blocked(login="three", ip="198.51.100.8")


@pytest.mark.asyncio
async def test_redis_backend_is_shared_between_workers() -> None:
    shared = FakeRedis()
    worker_a = _policy(RedisBackend("redis://unused", client=shared), pair_limit=2)
    worker_b = _policy(RedisBackend("redis://unused", client=shared), pair_limit=2)
    for _ in range(2):
        assert await worker_a.record_login_failure(login="owner", ip="203.0.113.11")
    # A second process sees the same counter atomically.
    assert await worker_b.login_blocked(login="owner", ip="203.0.113.11")
    assert not await worker_b.record_login_failure(login="owner", ip="203.0.113.11")


@pytest.mark.asyncio
async def test_window_expiry_releases_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(limiter_module.time, "time", lambda: clock["now"])
    limiter = _policy(MemoryBackend(), pair_limit=1, ip_limit=1)
    assert await limiter.record_login_failure(login="victim", ip="203.0.113.12")
    assert await limiter.login_blocked(login="victim", ip="203.0.113.12")
    clock["now"] += 601
    assert not await limiter.login_blocked(login="victim", ip="203.0.113.12")
    assert await limiter.record_login_failure(login="victim", ip="203.0.113.12")


@pytest.mark.asyncio
async def test_mfa_limits_are_separate_and_cleared_on_success() -> None:
    limiter = _policy(MemoryBackend(), mfa_session_limit=2, mfa_ip_limit=20)
    for _ in range(2):
        assert await limiter.record_mfa_failure(session_id="sess-1", ip="203.0.113.13")
    assert await limiter.mfa_blocked(session_id="sess-1", ip="203.0.113.13")
    assert not await limiter.mfa_blocked(session_id="sess-2", ip="203.0.113.13")
    await limiter.clear_mfa_failures(session_id="sess-1", ip="203.0.113.13")
    assert not await limiter.mfa_blocked(session_id="sess-1", ip="203.0.113.13")


@pytest.mark.asyncio
async def test_redis_failure_falls_back_or_fails_closed() -> None:
    class BrokenRedis:
        def pipeline(self, transaction: bool = True):  # noqa: ANN201, ARG002
            raise ConnectionError("down")

        async def ping(self) -> bool:
            return False

    broken = RedisBackend("redis://unused", client=BrokenRedis())
    fallback = MemoryBackend()
    lenient = _policy(broken, pair_limit=1, fallback=fallback)
    assert await lenient.record_login_failure(login="x", ip="203.0.113.14") is True
    assert await lenient.login_blocked(login="x", ip="203.0.113.14")

    strict = _policy(
        RedisBackend("redis://unused", client=BrokenRedis()),
        pair_limit=1,
        fallback=None,
        fail_closed=True,
    )
    with pytest.raises(limiter_module.RateLimiterUnavailable):
        await strict.login_blocked(login="x", ip="203.0.113.14")


def test_forwarded_for_is_ignored_without_a_trusted_proxy() -> None:
    assert (
        resolve_client_ip("203.0.113.20", "198.51.100.1, 203.0.113.5", trusted_proxies="")
        == "203.0.113.20"
    )
    # A non-proxy peer cannot spoof its address either.
    assert (
        resolve_client_ip("203.0.113.20", "203.0.113.5", trusted_proxies="127.0.0.1/32")
        == "203.0.113.20"
    )


def test_forwarded_for_uses_rightmost_untrusted_hop_behind_proxy() -> None:
    # Client-supplied prefix must not win: walk from the right.
    assert (
        resolve_client_ip(
            "127.0.0.1",
            "6.6.6.6, 203.0.113.77, 127.0.0.1",
            trusted_proxies="127.0.0.1/32, 198.51.100.0/24",
        )
        == "203.0.113.77"
    )
    # Malformed header falls back to the socket peer.
    assert (
        resolve_client_ip("127.0.0.1", "not-an-ip", trusted_proxies="127.0.0.1/32")
        == "127.0.0.1"
    )


def test_production_refuses_insecure_admin_settings() -> None:
    insecure_cookie = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="production",
        ADMIN_COOKIE_SECURE="false",
        RATE_LIMIT_BACKEND="redis",
    )
    with pytest.raises(RuntimeError, match="ADMIN_COOKIE_SECURE"):
        insecure_cookie.validate_admin_config()

    local_limiter = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="production",
        ADMIN_COOKIE_SECURE="true",
        RATE_LIMIT_BACKEND="memory",
    )
    with pytest.raises(RuntimeError, match="RATE_LIMIT_BACKEND=redis"):
        local_limiter.validate_admin_config()

    safe = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="production",
        ADMIN_COOKIE_SECURE="true",
        RATE_LIMIT_BACKEND="redis",
        REDIS_URL="redis://cache:6379/0",
    )
    safe.validate_admin_config()


def test_local_development_keeps_working_with_memory_limits() -> None:
    local = Settings(
        _env_file=None,
        BOT_TOKEN="x",
        ENVIRONMENT="development",
        ADMIN_COOKIE_SECURE="false",
        RATE_LIMIT_BACKEND="memory",
    )
    local.validate_admin_config()


def test_unknown_limiter_backend_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="RATE_LIMIT_BACKEND"):
        Settings(_env_file=None, BOT_TOKEN="x", RATE_LIMIT_BACKEND="files").validate_admin_config()


def _real_redis_url() -> str:
    if os.environ.get("RAUNDO_REDIS_SUITE_TESTS") != "1":
        return ""
    return os.environ.get("REDIS_URL", "")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _real_redis_url(), reason="Redis suite disabled (set RAUNDO_REDIS_SUITE_TESTS=1)"
)
async def test_real_redis_counts_are_shared_between_workers() -> None:
    import uuid

    from redis.asyncio import Redis

    url = _real_redis_url()
    suffix = uuid.uuid4().hex
    login, ip = f"redis-suite-{suffix}", "203.0.113.99"
    limiter_a = _policy(RedisBackend(url), pair_limit=2)
    limiter_b = _policy(RedisBackend(url), pair_limit=2)
    try:
        assert await limiter_a.record_login_failure(login=login, ip=ip)
        assert await limiter_a.record_login_failure(login=login, ip=ip)
        assert await limiter_b.login_blocked(login=login, ip=ip)
        assert not await limiter_b.record_login_failure(login=login, ip=ip)
        await limiter_b.clear_login_failures(login=login, ip=ip)
        assert not await limiter_a.login_blocked(login=login, ip=ip)
    finally:
        client = Redis.from_url(url, decode_responses=True)
        await client.delete(
            limiter_a._pair_key(login, ip),  # noqa: SLF001 - suite cleanup
            limiter_a._ip_key(ip),  # noqa: SLF001
        )
        await client.aclose()
