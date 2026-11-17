"""Regression tests for the public admin login/MFA in-memory limiter."""

from __future__ import annotations

import time

from raundo.admin_api.app import (
    _LOGIN_FAILS,
    _MFA_FAILS,
    _MAX_FAILURE_BUCKETS,
    _mfa_rate_ok,
    _rate_ok,
)


def test_login_failures_are_bounded_for_unknown_usernames() -> None:
    _LOGIN_FAILS.clear()
    try:
        now = time.time()
        for index in range(_MAX_FAILURE_BUCKETS):
            _LOGIN_FAILS[f"unrecognized-{index}"] = [now - 1000]
        assert _rate_ok("new-username")
        assert len(_LOGIN_FAILS) < _MAX_FAILURE_BUCKETS
        assert not any(name.startswith("unrecognized-") for name in _LOGIN_FAILS)

        for index in range(_MAX_FAILURE_BUCKETS + 50):
            login = f"active-{index}"
            assert _rate_ok(login)
            _LOGIN_FAILS[login] = [now]
        assert len(_LOGIN_FAILS) <= _MAX_FAILURE_BUCKETS
    finally:
        _LOGIN_FAILS.clear()


def test_login_and_mfa_attempt_limits_preserved() -> None:
    _LOGIN_FAILS.clear()
    _MFA_FAILS.clear()
    try:
        now = time.time()
        _LOGIN_FAILS["target"] = [now] * 8
        _MFA_FAILS["target"] = [now] * 10
        assert not _rate_ok("target")
        assert not _mfa_rate_ok("target")
        _LOGIN_FAILS["target"] = [now - 1000] * 8
        _MFA_FAILS["target"] = [now - 1000] * 10
        assert _rate_ok("target")
        assert _mfa_rate_ok("target")
    finally:
        _LOGIN_FAILS.clear()
        _MFA_FAILS.clear()
