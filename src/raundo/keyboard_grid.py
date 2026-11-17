"""Reusable Telegram inline grids: 1/2/3 columns, selection ticks, Done."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MAX_CALLBACK_BYTES = 64
MAX_BUTTON_CHARS = 64
DONE_LABEL = "✅ Готово"


def choose_columns(labels: list[str], *, preferred: int) -> int:
    """Prefer the requested density, but keep long captions readable."""
    if preferred < 1:
        return 1
    longest = max((len(label) for label in labels), default=0)
    if preferred >= 3 and longest <= 14:
        return 3
    if preferred >= 2 and longest <= 24:
        return 2
    if longest > 28:
        return 1
    return min(preferred, 2)


def _fit_label(text: str) -> str:
    value = text.strip()
    if len(value) <= MAX_BUTTON_CHARS:
        return value
    return value[: MAX_BUTTON_CHARS - 1].rstrip() + "…"


def _fit_callback(data: str) -> str:
    raw = data.encode("utf-8")
    if len(raw) <= MAX_CALLBACK_BYTES:
        return data
    raise ValueError(f"callback_data exceeds {MAX_CALLBACK_BYTES} bytes")


def grid_markup(
    items: list[tuple[str, str]],
    *,
    prefix: str,
    columns: int,
    selected: set[str] | None = None,
    done_callback: str | None = None,
) -> InlineKeyboardMarkup:
    """Build an inline grid. `items` are (code, visible_label). Codes stay in callback."""
    chosen = set(selected or [])
    labels = [label for _code, label in items]
    cols = max(1, choose_columns(labels, preferred=columns))
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code, label in items:
        mark = "✅ " if code in chosen else ""
        row.append(
            InlineKeyboardButton(
                text=_fit_label(f"{mark}{label}"),
                callback_data=_fit_callback(f"{prefix}{code}"),
            )
        )
        if len(row) >= cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if done_callback:
        rows.append(
            [InlineKeyboardButton(text=DONE_LABEL, callback_data=_fit_callback(done_callback))]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def single_choice_grid(
    items: list[tuple[str, str]],
    *,
    prefix: str,
    columns: int,
) -> InlineKeyboardMarkup:
    return grid_markup(items, prefix=prefix, columns=columns, selected=None, done_callback=None)
