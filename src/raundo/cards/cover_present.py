"""Display-only cover presentation. Never mutates stored startup_media bytes."""

from __future__ import annotations

import io
import logging
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile

from raundo.cards.startup_card import StartupCardView, cover_needs_letterbox

logger = logging.getLogger(__name__)

# Soft Apple-like fill for letterbox gutters (not pure white glare).
_PAD_RGB = (245, 245, 247)
_TARGET_W = 1280
_TARGET_H = 720  # 16:9


async def resolve_card_photo(bot: Bot, view: StartupCardView) -> Any:
    """
    Return a value suitable for ``answer_photo(photo=...)``.

    - Landscape covers: original ``file_id`` (no re-encode).
    - Square / portrait / unknown: temporary 16:9 letterbox upload for display only.
    - On any failure: fall back to original ``file_id``.
    Stored Telegram originals and DB rows are never overwritten.
    """
    file_id = view.cover_file_id
    if not file_id:
        return None
    if not cover_needs_letterbox(view.cover_width, view.cover_height):
        return file_id
    try:
        return await _letterbox_file_id(bot, file_id)
    except Exception:  # noqa: BLE001 — display fallback must not break preview
        logger.warning("cover letterbox failed; using original file_id", exc_info=True)
        return file_id


async def _letterbox_file_id(bot: Bot, file_id: str) -> BufferedInputFile:
    from PIL import Image  # lazy: optional path only when letterbox needed

    tg_file = await bot.get_file(file_id)
    if not tg_file.file_path:
        raise RuntimeError("Telegram file_path missing")
    buf = io.BytesIO()
    await bot.download_file(tg_file.file_path, destination=buf)
    buf.seek(0)
    with Image.open(buf) as src:
        image = src.convert("RGBA")
    canvas = Image.new("RGB", (_TARGET_W, _TARGET_H), _PAD_RGB)
    # Fit inside 16:9 without cropping or stretching.
    scale = min(_TARGET_W / image.width, _TARGET_H / image.height)
    new_w = max(1, int(image.width * scale))
    new_h = max(1, int(image.height * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if resized.mode == "RGBA":
        background = Image.new("RGBA", resized.size, (*_PAD_RGB, 255))
        composed = Image.alpha_composite(background, resized).convert("RGB")
    else:
        composed = resized.convert("RGB")
    offset = ((_TARGET_W - new_w) // 2, (_TARGET_H - new_h) // 2)
    canvas.paste(composed, offset)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88, optimize=True)
    return BufferedInputFile(out.getvalue(), filename="cover.jpg")
