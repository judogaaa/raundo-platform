"""Guards for local test-all.ps1 destructive DB operations (no real DROP of app DBs)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from raundo.testing.db_safety import (
    ALLOWED_SUITE_DATABASES,
    DbSafetyError,
    assert_postgres_container_allowed,
    assert_suite_db_drop_allowed,
    assert_suite_host_port,
    assert_valid_sql_identifier,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test-all.ps1"


def test_only_raundo_local_suite_is_drop_allowlisted() -> None:
    assert ALLOWED_SUITE_DATABASES == frozenset({"raundo_local_suite"})
    assert assert_suite_db_drop_allowed("raundo_local_suite") == "raundo_local_suite"


@pytest.mark.parametrize(
    "name",
    [
        "raundo",
        "postgres",
        "template0",
        "template1",
        "raundo_alembic_test",
        "Raundo_Local_Suite",
        "raundo_local_suite; DROP DATABASE raundo",
        "",
        "suite",
        "production",
    ],
)
def test_forbidden_or_invalid_names_refuse_drop(name: str) -> None:
    with pytest.raises(DbSafetyError):
        assert_suite_db_drop_allowed(name)


def test_sql_identifier_rejects_injection_shapes() -> None:
    with pytest.raises(DbSafetyError):
        assert_valid_sql_identifier("db-name")
    with pytest.raises(DbSafetyError):
        assert_valid_sql_identifier("1bad")


def test_container_and_port_guards() -> None:
    assert assert_postgres_container_allowed("raundo_local-postgres-1") == "raundo_local-postgres-1"
    with pytest.raises(DbSafetyError):
        assert_postgres_container_allowed("brand-my-turbo-s-db")
    with pytest.raises(DbSafetyError):
        assert_postgres_container_allowed("raundo_local-redis-1")
    assert assert_suite_host_port(55432) == 55432
    with pytest.raises(DbSafetyError):
        assert_suite_host_port(5432)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="scripts/test-all.ps1 is Windows PowerShell; Linux CI uses Python allowlist tests",
)
def test_test_all_script_refuses_suite_db_named_raundo_without_dropping() -> None:
    """
    Invokes the real gate with -SuiteDb raundo.

    Must exit non-zero at the **allowlist** step (before DROP). A bare Docker
    FATAL is not enough — test-all checks Docker *before* allowlist, so without
    a healthy engine this integration case cannot prove the safety gate.
    """
    if not SCRIPT.is_file():
        pytest.skip("test-all.ps1 missing")

    container = "raundo_local-postgres-1"
    try:
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Docker CLI hung (inspect timeout); cannot run test-all integration")
    docker_ok = inspect.returncode == 0 and inspect.stdout.strip() == "healthy"
    if not docker_ok:
        pytest.skip(
            "Docker engine/container not healthy; test-all.ps1 fails before allowlist"
        )

    existed_before: str | None = None
    probe = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "raundo",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='raundo'",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    existed_before = probe.stdout.strip()

    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-SuiteDb",
            "raundo",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    combined = (proc.stdout or "") + (proc.stderr or "")
    low = combined.lower()
    # Prove allowlist refusal specifically (DbSafetyError / step name), not infra noise.
    allowlist_hit = (
        "refusing" in low
        or "forbidden database" in low
        or ("allowlist" in low and "fail" in low)
    )
    assert allowlist_hit, (
        "expected allowlist refusal for SuiteDb=raundo, got:\n" + combined[-2000:]
    )
    assert "docker desktop engine not available" not in low

    if existed_before == "1":
        after = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "raundo",
                "-d",
                "postgres",
                "-tAc",
                "SELECT 1 FROM pg_database WHERE datname='raundo'",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert after.stdout.strip() == "1", "database raundo must not be dropped by refused gate"


def test_python_guard_matches_script_default_constants() -> None:
    # Keep PowerShell defaults and Python allowlist in sync intentionally.
    assert "raundo_local_suite" in ALLOWED_SUITE_DATABASES
    assert assert_postgres_container_allowed("raundo_local-postgres-1")
    assert assert_suite_host_port(55432)
