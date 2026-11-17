"""Hard guard: admin E2E must never touch the live bot database."""

from __future__ import annotations

import re
from urllib.parse import urlparse

E2E_ALLOWED_DATABASES: frozenset[str] = frozenset({"raundo_admin_dev"})
LIVE_DATABASE = "raundo"
DEFAULT_E2E_DB_URL = (
    "postgresql+asyncpg://raundo:raundo_local_only@127.0.0.1:55432/raundo_admin_dev"
)


class E2EGuardError(RuntimeError):
    """Raised when an E2E/test process is pointed at a forbidden database."""


def db_name_from_url(url: str) -> str:
    text = (url or "").strip()
    # Strip SQLAlchemy driver suffix and query string before URL parsing.
    text = re.sub(r"^postgresql\+\w+://", "postgresql://", text)
    parsed = urlparse(text)
    name = parsed.path.lstrip("/").split("/")[0]
    if "?" in name:
        name = name.split("?", 1)[0]
    return name


def assert_e2e_db_url(url: str) -> str:
    """Allow only the dedicated admin dev DB; refuse live/suite/unknown names."""
    name = db_name_from_url(url)
    if name == LIVE_DATABASE:
        raise E2EGuardError(
            "E2E refuses the live database 'raundo'. Tests must use raundo_admin_dev."
        )
    if name not in E2E_ALLOWED_DATABASES:
        raise E2EGuardError(
            f"E2E refuses database {name!r}; allowed: {sorted(E2E_ALLOWED_DATABASES)}."
        )
    return name


def assert_e2e_actual_db(actual: str) -> str:
    """Second check against SELECT current_database() inside the running API DB."""
    text = (actual or "").strip()
    if text == LIVE_DATABASE:
        raise E2EGuardError(
            "E2E API is connected to the live database 'raundo'; aborting before any request."
        )
    if text not in E2E_ALLOWED_DATABASES:
        raise E2EGuardError(
            f"E2E API connected to unexpected database {text!r}; aborting."
        )
    return text
