"""Validation helpers for Issue #2 profile forms."""

from raundo.domain import (
    can_edit_review_state,
    can_submit_review_state,
    cheque_is_relevant,
    investor_needs_organization,
    major_units_to_minor,
    normalize_https_url,
    parse_cheque_range_major,
    parse_single_amount_major,
    slugify_startup_name,
    validate_cheque_bounds,
    validate_description,
    validate_investor_preferences,
    validate_name,
    validate_pitch,
    validate_startup_photo_count,
    validate_strategy,
)
from raundo.participants import PARTICIPANT_TYPE_LABELS, canonical_participant_type


def test_field_length_validators() -> None:
    assert validate_name("A") is not None
    assert validate_name("OK") is None
    assert validate_pitch("too short") is not None
    assert validate_pitch("x" * 49) is not None
    assert validate_pitch("x" * 50) is None
    assert validate_pitch("x" * 120) is None
    assert validate_pitch("x" * 121) is not None
    legacy = "y" * 240
    assert validate_pitch(legacy, previous=legacy) is None
    assert validate_pitch(legacy[:-1], previous=legacy) is not None
    assert validate_description("x" * 50) is not None
    assert validate_description("x" * 100) is None
    assert validate_description("x" * 500) is None
    assert validate_description("x" * 501) is not None
    assert validate_strategy("x" * 50) is not None
    assert validate_strategy("x" * 100) is None


def test_https_url_normalization() -> None:
    assert normalize_https_url("https://example.com/app") == "https://example.com/app"
    assert normalize_https_url("example.com") == "https://example.com"
    assert normalize_https_url("http://insecure.example") is None
    assert normalize_https_url("not a url") is None
    assert normalize_https_url("https://user:pass@example.com/secret") is None
    assert normalize_https_url("https://example.com/ok") == "https://example.com/ok"


def test_money_minor_units_and_ranges() -> None:
    assert major_units_to_minor(15_000_000, "RUB") == 1_500_000_000
    assert parse_single_amount_major("15000000 RUB") == (1_500_000_000, "RUB")
    assert parse_cheque_range_major("100000 5000000 RUB") == (
        10_000_000,
        500_000_000,
        "RUB",
    )
    assert parse_cheque_range_major("5000000 100000 RUB") is None  # min > max
    assert parse_single_amount_major("100 XYZ") is None
    assert validate_cheque_bounds(
        min_minor=100, max_minor=50, currency="RUB", discuss=False
    ) is not None
    assert (
        validate_cheque_bounds(
            min_minor=None, max_minor=None, currency=None, discuss=True
        )
        is None
    )


def test_photo_bounds_and_preferences() -> None:
    assert not validate_startup_photo_count(0)
    assert validate_startup_photo_count(1)
    assert validate_startup_photo_count(3)
    assert not validate_startup_photo_count(4)
    assert validate_investor_preferences({"ai_ml"}, {"mvp"})
    assert not validate_investor_preferences(set(), {"mvp"})


def test_review_state_gates_and_org_rules() -> None:
    assert can_edit_review_state("draft")
    assert can_submit_review_state("draft")
    assert can_edit_review_state("submitted")
    assert not can_submit_review_state("submitted")
    assert can_edit_review_state("pending_changes")
    assert can_submit_review_state("pending_changes")
    assert investor_needs_organization("fund")
    assert investor_needs_organization("vc_fund")
    assert not investor_needs_organization("angel")
    assert slugify_startup_name("Hello World!") == "hello-world"


def test_participant_types_and_cheque_relevance() -> None:
    assert "accelerator" in PARTICIPANT_TYPE_LABELS
    assert "vc_fund" in PARTICIPANT_TYPE_LABELS
    assert canonical_participant_type("fund") == "vc_fund"
    assert canonical_participant_type("corporate") == "cvc"
    assert cheque_is_relevant("angel", ["direct_equity"])
    assert not cheque_is_relevant("accelerator", ["program", "mentorship"])
    assert cheque_is_relevant("accelerator", ["direct_equity", "program"])
