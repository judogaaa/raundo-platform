"""Staff-only access to startup gallery bytes without exposing Bot tokens to the browser."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raundo.models.admin_panel import SupportMessage
from raundo.models.investor import InvestorProfile
from raundo.models.startup import Startup, StartupMedia


class SupportMediaError(Exception):
    """Attachment is missing or Telegram cannot provide it right now."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _telegram_proxy() -> str | None:
    """Telegram file downloads follow the same egress policy as the Bot API."""
    try:
        from raundo.config import get_settings

        value = (get_settings().telegram_proxy or "").strip()
    except Exception:  # noqa: BLE001 - settings must never break media serving
        return None
    return value or None


async def fetch_support_attachment(
    session: AsyncSession, message_id: uuid.UUID
) -> tuple[bytes, str]:
    message = await session.get(SupportMessage, message_id)
    if message is None or not message.attachment_file_id:
        raise SupportMediaError("missing")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SupportMediaError("no_token")
    async with httpx.AsyncClient(timeout=20.0, proxy=_telegram_proxy()) as client:
        try:
            file_resp = await client.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": message.attachment_file_id},
            )
            file_resp.raise_for_status()
            file_path = file_resp.json()["result"]["file_path"]
            data_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
            data_resp.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SupportMediaError("telegram") from exc
        content_type = _image_type(
            data_resp.content,
            data_resp.headers.get("content-type"),
            file_path,
        )
        return data_resp.content, content_type


def _image_type(data: bytes, content_type: str | None, path: str) -> str:
    """Telegram CDN often returns octet-stream; browsers need a real image type."""
    if content_type and content_type.startswith("image/"):
        return content_type
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if path.lower().endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if path.lower().endswith(".png"):
        return "image/png"
    if path.lower().endswith(".webp"):
        return "image/webp"
    return "application/octet-stream"


def _demo_media_path(file_id: str) -> "Path | None":
    """Local demo assets: file_id == 'demo:<filename>' (never sent to Telegram)."""
    if not file_id.startswith("demo:"):
        return None
    name = file_id.split(":", 1)[1].strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    root = Path(__file__).resolve().parents[3] / "private_media" / "demo"
    path = (root / name).resolve()
    if root.resolve() not in path.parents:
        return None
    return path if path.is_file() else None


async def fetch_startup_photo(
    session: AsyncSession,
    startup_id: uuid.UUID,
    position: int,
    *,
    allow_deleted: bool = False,
) -> tuple[bytes, str]:
    row = await session.execute(
        select(StartupMedia).where(
            StartupMedia.startup_id == startup_id,
            StartupMedia.position == position,
        )
    )
    media = row.scalar_one_or_none()
    if media is None:
        raise FileNotFoundError("media")
    startup = await session.get(Startup, startup_id)
    if startup is None or (startup.deleted_at is not None and not allow_deleted):
        raise FileNotFoundError("startup")
    demo_path = _demo_media_path(media.telegram_file_id or "")
    if demo_path is not None:
        data = demo_path.read_bytes()
        return data, _image_type(data, None, demo_path.name)
    if media.telegram_file_id and media.telegram_file_id.startswith("demo:"):
        raise FileNotFoundError("demo_asset")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
            f'<rect width="100%" height="100%" fill="#e5e5ea"/>'
            f'<text x="50%" y="50%" text-anchor="middle" fill="#6e6e73" font-size="14">'
            f"Фото {position}</text></svg>"
        )
        return svg.encode("utf-8"), "image/svg+xml"
    async with httpx.AsyncClient(timeout=20.0, proxy=_telegram_proxy()) as client:
        file_resp = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": media.telegram_file_id},
        )
        file_resp.raise_for_status()
        file_path = file_resp.json()["result"]["file_path"]
        data_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        data_resp.raise_for_status()
        content_type = _image_type(
            data_resp.content,
            data_resp.headers.get("content-type"),
            file_path,
        )
        return data_resp.content, content_type


async def fetch_investor_photo(session: AsyncSession, investor_id: uuid.UUID) -> tuple[bytes, str]:
    profile = await session.get(InvestorProfile, investor_id)
    if profile is None or profile.deleted_at is not None or not profile.telegram_photo_file_id:
        raise FileNotFoundError("media")
    demo_path = _demo_media_path(profile.telegram_photo_file_id)
    if demo_path is not None:
        data = demo_path.read_bytes()
        return data, _image_type(data, None, demo_path.name)
    if profile.telegram_photo_file_id.startswith("demo:"):
        raise FileNotFoundError("demo_asset")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
            '<rect width="100%" height="100%" fill="#e5e5ea"/>'
            '<text x="50%" y="50%" text-anchor="middle" fill="#6e6e73" font-size="14">'
            "Фото инвестора</text></svg>"
        )
        return svg.encode("utf-8"), "image/svg+xml"
    async with httpx.AsyncClient(timeout=20.0, proxy=_telegram_proxy()) as client:
        file_resp = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": profile.telegram_photo_file_id},
        )
        file_resp.raise_for_status()
        file_path = file_resp.json()["result"]["file_path"]
        data_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        data_resp.raise_for_status()
        content_type = _image_type(
            data_resp.content,
            data_resp.headers.get("content-type"),
            file_path,
        )
        return data_resp.content, content_type
