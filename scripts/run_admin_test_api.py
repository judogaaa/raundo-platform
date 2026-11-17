"""Start the E2E admin API after proving it is attached to the dev database."""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text

from raundo.db.session import get_session_factory
from raundo.testing.e2e_guard import assert_e2e_actual_db, assert_e2e_db_url

PORT = int(os.environ.get("RAUNDO_E2E_API_PORT", "8091"))


async def _check_actual_db() -> str:
    from raundo.db import session as db_session_module

    factory = get_session_factory()
    async with factory() as session:
        actual = await session.scalar(text("SELECT current_database()"))
    # The guard runs in its own event loop; dispose the pool before uvicorn
    # starts a new loop, otherwise the first real requests hit closed connections.
    engine = factory.kw.get("bind")
    if engine is not None:
        await engine.dispose()
    db_session_module._cached_factory = None
    return str(actual or "")


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    try:
        expected = assert_e2e_db_url(url)
        actual = asyncio.run(_check_actual_db())
        assert_e2e_actual_db(actual)
    except Exception as exc:  # noqa: BLE001
        print(f"E2E ISOLATION REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"E2E DB guard OK: expected={expected} actual={actual} port={PORT}", flush=True)

    import uvicorn

    uvicorn.run(
        "raundo.admin_api.app:app",
        host="127.0.0.1",
        port=PORT,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
