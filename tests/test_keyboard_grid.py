"""Inline grid density, ticks, Done callback."""

from raundo.keyboard_grid import choose_columns, grid_markup
from raundo.wizard_keyboards import (
    progress_toggle_keyboard,
    sector_toggle_keyboard,
    use_of_funds_keyboard,
)


def test_choose_columns_keeps_long_labels_readable() -> None:
    assert choose_columns(["AI/ML", "SaaS/B2B", "FinTech"], preferred=3) == 3
    assert choose_columns(["Корпоративный венчурный фонд"], preferred=3) <= 2
    assert choose_columns(["Продукт", "Команда"], preferred=2) == 2


def test_grid_marks_selected_and_done() -> None:
    markup = grid_markup(
        [("a", "A"), ("b", "B"), ("c", "C")],
        prefix="w:x:",
        columns=3,
        selected={"b"},
        done_callback="w:done:x",
    )
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any(t.startswith("✅ B") for t in labels)
    assert labels[-1] == "✅ Готово"
    assert all(len(btn.callback_data.encode()) <= 64 for row in markup.inline_keyboard for btn in row)


def test_progress_and_funds_use_two_columns() -> None:
    progress = progress_toggle_keyboard({"mvp"})
    funds = use_of_funds_keyboard({"product"})
    assert max(len(row) for row in progress.inline_keyboard[:-1]) == 2
    assert max(len(row) for row in funds.inline_keyboard[:-1]) == 2


def test_sector_grid_up_to_three_columns() -> None:
    items = [(f"c{i}", f"Cat{i}") for i in range(6)]
    markup = sector_toggle_keyboard(items, {"c1"})
    body = markup.inline_keyboard[:-1]
    assert max(len(row) for row in body) == 3
