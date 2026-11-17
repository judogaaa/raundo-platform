"""Process runtime diagnostics (no secrets). Captured once at import/start."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

APP_NAME = "RAUNDO"

# Wall-clock start of this Python process (set when module is first imported).
PROCESS_STARTED_AT = datetime.now(timezone.utc)


def _repo_root_from_package() -> Path | None:
    """src/raundo/runtime_info.py → repo root for editable src layout."""
    here = Path(__file__).resolve()
    # .../RAUNDO/src/raundo/runtime_info.py → parents[0]=raundo, [1]=src, [2]=repo
    if here.parent.name != "raundo":
        return None
    candidate = here.parents[2] if len(here.parents) >= 3 else None
    if candidate is None:
        return None
    if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
        return candidate
    return None


@lru_cache(maxsize=1)
def resolve_git_sha() -> str | None:
    """Best-effort SHA of the running code. None if unknown.

    Containers have no .git, so deployments set RAUNDO_BUILD_SHA (the image tag,
    which is the source commit) and that value wins.
    """
    for env_name in ("RAUNDO_BUILD_SHA", "GIT_SHA", "RAUNDO_RELEASE_SHA"):
        value = (os.environ.get(env_name) or "").strip()
        if len(value) >= 7 and value.lower() not in {"unknown", "local", "latest"}:
            return value
    root = _repo_root_from_package()
    if root is None:
        return None
    git_exe = "git"
    try:
        out = subprocess.check_output(
            [git_exe, "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Windows: git may only be on PATH via shell; try where.exe once.
        try:
            where = subprocess.check_output(
                ["where", "git"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            git_exe = (where.splitlines() or [""])[0].strip() or "git"
            out = subprocess.check_output(
                [git_exe, "-C", str(root), "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    sha = (out or "").strip()
    return sha if len(sha) >= 7 else None


def format_version_report() -> str:
    """Human-readable /version body. Never includes tokens or connection strings."""
    sha = resolve_git_sha()
    sha_line = sha if sha else "не удалось достоверно определить (git недоступен)"
    started = PROCESS_STARTED_AT.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    return "\n".join(
        [
            f"Приложение: {APP_NAME}",
            f"Git SHA: {sha_line}",
            f"Старт процесса: {started}",
            f"PID: {os.getpid()}",
            f"Python: {sys.version.split()[0]}",
        ]
    )
