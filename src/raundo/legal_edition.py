"""Single source of the legal edition used by consent checks.

The edition lives in `legal/manifest.json`; `POLICY_VERSION` from the
environment is only a fallback for tests and local runs without the bundle.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

FALLBACK_EDITION = "1.0"


def legal_dir() -> Path:
    return Path(
        os.environ.get("LEGAL_DIST")
        or (Path(__file__).resolve().parents[2] / "legal")
    )


@lru_cache(maxsize=1)
def legal_edition() -> str:
    """Edition used for consent checks.

    `consent_edition` changes only when the scope of consent changes; editorial
    updates (contacts, hosting) bump `edition` without forcing re-consent.
    """
    path = legal_dir() / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FALLBACK_EDITION
    for key in ("consent_edition", "edition"):
        edition = str(manifest.get(key) or "").strip()
        if edition:
            return edition
    return FALLBACK_EDITION


def clear_cache() -> None:
    legal_edition.cache_clear()
