"""PostgreSQL-only checks for migration 20261026_0005 (grandfathered_gallery).

Runs when test-all sets RAUNDO_POSTGRES_SUITE_TESTS=1 against allowlisted
raundo_local_suite (never the live raundo database).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from raundo.testing.db_safety import assert_suite_db_drop_allowed

REPO_ROOT = Path(__file__).resolve().parents[1]
REV_BEFORE_PREMIUM = "20261023_0004"

BACKFILL_SQL = (
    "UPDATE startups SET grandfathered_gallery = true "
    "WHERE (SELECT COUNT(*) FROM startup_media "
    "WHERE startup_media.startup_id = startups.id AND startup_media.kind = 'photo') >= 2"
)


def _suite_tests_enabled() -> bool:
    if os.environ.get("RAUNDO_POSTGRES_SUITE_TESTS") != "1":
        return False
    url = os.environ.get("DATABASE_URL", "")
    if "raundo_local_suite" not in url:
        return False
    assert_suite_db_drop_allowed("raundo_local_suite")
    return True


def _run_alembic(*args: str) -> None:
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


async def _alembic_version(session: AsyncSession) -> str | None:
    try:
        return (
            await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        ).scalar_one_or_none()
    except Exception:
        return None


async def _seed_legacy_galleries(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Pre–0005 rows via SQL so ORM does not reference grandfathered_gallery yet."""
    owner_id = uuid.uuid4()
    telegram_id = 880_000 + (uuid.uuid4().int % 100_000)
    await session.execute(
        text(
            """
            INSERT INTO users (id, telegram_id, username, first_name, locale, status)
            VALUES (:id, :telegram_id, 'migtest', 'M', 'ru', 'active')
            """
        ),
        {"id": owner_id, "telegram_id": telegram_id},
    )
    ids: dict[str, uuid.UUID] = {}
    media_rows: list[tuple[uuid.UUID, str, str, int]] = []
    for key, name, photo_count in (
        ("zapas", "ZAPAS-like", 3),
        ("two", "TwoPhotos", 2),
        ("one", "OnePhoto", 1),
    ):
        sid = uuid.uuid4()
        ids[key] = sid
        await session.execute(
            text(
                """
                INSERT INTO startups (
                    id, owner_user_id, slug, name, review_state, contact_policy,
                    intents, audience_codes, progress_codes, use_of_funds_codes,
                    current_revision, show_contact_after_accept
                )
                VALUES (
                    :id, :owner_id, :slug, :name, 'draft', 'relay',
                    '[]'::json, '[]'::json, '[]'::json, '[]'::json,
                    1, false
                )
                """
            ),
            {
                "id": sid,
                "owner_id": owner_id,
                "slug": f"legacy-{key}-{sid.hex[:8]}",
                "name": name,
            },
        )
        for pos in range(1, photo_count + 1):
            mid = uuid.uuid4()
            fid = f"file-{sid}-{pos}"
            uid = f"uniq-{sid}-{pos}"
            media_rows.append((sid, fid, uid, pos))
            await session.execute(
                text(
                    """
                    INSERT INTO startup_media (
                        id, startup_id, telegram_file_id, file_unique_id, position, kind,
                        scan_state, visibility
                    )
                    VALUES (:id, :startup_id, :fid, :uid, :pos, 'photo', 'pending', 'public')
                    """
                ),
                {
                    "id": mid,
                    "startup_id": sid,
                    "fid": fid,
                    "uid": uid,
                    "pos": pos,
                },
            )
    await session.commit()
    return ids


@pytest.mark.asyncio
async def test_premium_migration_grandfather_backfill_on_postgres() -> None:
    if not _suite_tests_enabled():
        pytest.skip("postgres suite migration tests disabled")

    from raundo.config import get_settings

    get_settings.cache_clear()
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        head = await _alembic_version(session)
    if head != "20261027_0007":
        pytest.skip(
            f"migration test runs only after alembic upgrade head on suite (got {head!r})"
        )

    try:
        _run_alembic("downgrade", REV_BEFORE_PREMIUM)

        async with factory() as session:
            ver = await _alembic_version(session)
            assert ver == REV_BEFORE_PREMIUM

        async with factory() as session:
            ids = await _seed_legacy_galleries(session)
            media_before = (
                await session.execute(
                    text(
                        """
                        SELECT telegram_file_id, file_unique_id, position
                        FROM startup_media
                        WHERE startup_id = :sid AND kind = 'photo'
                        ORDER BY position
                        """
                    ),
                    {"sid": ids["zapas"]},
                )
            ).all()
            assert len(media_before) == 3

        _run_alembic("upgrade", "head")

        async with factory() as session:
            zapas = (
                await session.execute(
                    text(
                        "SELECT grandfathered_gallery FROM startups WHERE id = :id"
                    ),
                    {"id": ids["zapas"]},
                )
            ).scalar_one()
            two = (
                await session.execute(
                    text("SELECT grandfathered_gallery FROM startups WHERE id = :id"),
                    {"id": ids["two"]},
                )
            ).scalar_one()
            one = (
                await session.execute(
                    text("SELECT grandfathered_gallery FROM startups WHERE id = :id"),
                    {"id": ids["one"]},
                )
            ).scalar_one()
            assert zapas is True
            assert two is True
            assert one is False

            media_after = (
                await session.execute(
                    text(
                        """
                        SELECT telegram_file_id, file_unique_id, position
                        FROM startup_media
                        WHERE startup_id = :sid AND kind = 'photo'
                        ORDER BY position
                        """
                    ),
                    {"sid": ids["zapas"]},
                )
            ).all()
            assert media_after == media_before

            await session.execute(text(BACKFILL_SQL))
            await session.execute(text(BACKFILL_SQL))
            await session.commit()
            one_again = (
                await session.execute(
                    text("SELECT grandfathered_gallery FROM startups WHERE id = :id"),
                    {"id": ids["one"]},
                )
            ).scalar_one()
            zapas_again = (
                await session.execute(
                    text("SELECT grandfathered_gallery FROM startups WHERE id = :id"),
                    {"id": ids["zapas"]},
                )
            ).scalar_one()
            assert one_again is False
            assert zapas_again is True

            offers = (
                await session.execute(
                    text("SELECT COUNT(*) FROM premium_plan_offers WHERE is_active")
                )
            ).scalar_one()
            assert offers == 12

            ver = await _alembic_version(session)
            assert ver == "20261027_0007"
    finally:
        try:
            _run_alembic("upgrade", "head")
        except AssertionError as exc:
            pytest.fail(f"suite DB left below head; restore failed: {exc}")
        await engine.dispose()
        get_settings.cache_clear()
