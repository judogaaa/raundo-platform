"""Money parser must reject NaN/Infinity without raising."""

from __future__ import annotations

from raundo.domain import (
    apply_investment_ask_step,
    major_units_to_minor,
    parse_cheque_range_major,
    parse_single_amount_major,
)


def test_parse_amount_rejects_nan_infinity_without_raise() -> None:
    for raw in (
        "NaN RUB",
        "nan RUB",
        "Infinity RUB",
        "-Infinity RUB",
        "inf RUB",
        "-inf RUB",
        "1e999999 RUB",
    ):
        assert parse_single_amount_major(raw) is None


def test_parse_cheque_rejects_nan_infinity_without_raise() -> None:
    for raw in (
        "NaN 100 RUB",
        "100 NaN RUB",
        "1 Infinity RUB",
        "Infinity 1 RUB",
    ):
        assert parse_cheque_range_major(raw) is None


def test_major_units_to_minor_rejects_non_finite() -> None:
    from decimal import Decimal

    assert major_units_to_minor(Decimal("NaN"), "RUB") is None
    assert major_units_to_minor(Decimal("Infinity"), "RUB") is None
    assert major_units_to_minor(-1, "RUB") is None
    assert major_units_to_minor(15_000_000, "RUB") == 1_500_000_000


def test_investment_ask_step_nan_returns_none() -> None:
    assert (
        apply_investment_ask_step(
            "NaN RUB",
            current_minor=1,
            current_currency="RUB",
            clear_label="CLEAR",
            skip_label="SKIP",
        )
        is None
    )
