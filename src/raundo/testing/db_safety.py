"""Hard guards for local test-suite database DROP/CREATE.

Only the dedicated RAUNDO suite database may be destroyed by developer scripts.
"""

from __future__ import annotations

import re

# Exact allowlist — never expand casually; scripts must refuse everything else.
ALLOWED_SUITE_DATABASES: frozenset[str] = frozenset({"raundo_local_suite"})

# Names that must never be dropped by the local gate (defense in depth).
FORBIDDEN_DROP_DATABASES: frozenset[str] = frozenset(
    {
        "postgres",
        "template0",
        "template1",
        "raundo",  # default app DB name in compose — not the suite
        "raundo_admin_dev",  # persistent local admin console DB; never dropped by the suite gate
        "raundo_alembic_test",  # prior ad-hoc migrate DB; do not wipe via suite gate
    }
)

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

ALLOWED_POSTGRES_CONTAINERS: frozenset[str] = frozenset({"raundo_local-postgres-1"})
DEFAULT_SUITE_HOST_PORT = 55432


class DbSafetyError(ValueError):
    """Raised when a destructive DB operation is refused."""


def assert_valid_sql_identifier(name: str) -> str:
    text = (name or "").strip()
    if not _IDENT_RE.match(text):
        raise DbSafetyError(
            f"Invalid database identifier {name!r}: expected lowercase SQL ident."
        )
    return text


def assert_suite_db_drop_allowed(name: str) -> str:
    """
    Refuse DROP/CREATE unless name is exactly an allowlisted suite DB.

    Negative cases (must raise): ``raundo``, ``postgres``, typos, empty, mixed case.
    """
    text = assert_valid_sql_identifier(name)
    if text in FORBIDDEN_DROP_DATABASES:
        raise DbSafetyError(
            f"Refusing destructive operation on forbidden database {text!r}."
        )
    if text not in ALLOWED_SUITE_DATABASES:
        raise DbSafetyError(
            f"Refusing DROP/CREATE of {text!r}; only {sorted(ALLOWED_SUITE_DATABASES)} allowed."
        )
    return text


def assert_postgres_container_allowed(container_name: str) -> str:
    text = (container_name or "").strip()
    if text not in ALLOWED_POSTGRES_CONTAINERS:
        raise DbSafetyError(
            f"Refusing DB ops on container {text!r}; "
            f"allowed: {sorted(ALLOWED_POSTGRES_CONTAINERS)}."
        )
    return text


def assert_suite_host_port(port: int, *, expected: int = DEFAULT_SUITE_HOST_PORT) -> int:
    if int(port) != int(expected):
        raise DbSafetyError(
            f"Refusing DB ops on host port {port}; expected RAUNDO suite port {expected} "
            "(host 5432 belongs to other projects)."
        )
    return int(port)
