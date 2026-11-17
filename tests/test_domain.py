"""Unit tests for invariant pure functions; no Telegram token required."""

from raundo.domain import (
    can_receive_verified_investor_label,
    can_share_private_contact,
    can_show_in_investor_feed,
    is_listed_project,
    validate_investor_preferences,
    validate_startup_photo_count,
)


def test_startup_gallery_requires_one_to_three() -> None:
    assert not validate_startup_photo_count(0)
    assert all(validate_startup_photo_count(n) for n in (1, 2, 3))
    assert not validate_startup_photo_count(4)


def test_investor_profile_needs_sector_and_stage() -> None:
    assert validate_investor_preferences({"ai"}, {"mvp"})
    assert not validate_investor_preferences(set(), {"mvp"})
    assert not validate_investor_preferences({"ai"}, set())


def test_review_is_required_to_show_startup_or_verified_investor() -> None:
    assert is_listed_project("published")
    assert not is_listed_project("submitted")
    assert not is_listed_project("rejected")
    assert can_receive_verified_investor_label("approved")
    assert not can_receive_verified_investor_label("pending")


def test_private_contact_requires_explicit_consents() -> None:
    assert can_share_private_contact(
        request_state="accepted",
        founder_consented=True,
        requester_consented=True,
    )
    assert not can_share_private_contact(
        request_state="pending",
        founder_consented=True,
        requester_consented=True,
    )
    assert not can_share_private_contact(
        request_state="accepted",
        founder_consented=False,
        requester_consented=True,
    )
    assert not can_share_private_contact(
        request_state="accepted",
        founder_consented=True,
        requester_consented=False,
    )


def test_investor_default_sector_stage_filter_and_explicit_all() -> None:
    args = {
        "startup_sectors": {"ai", "health"},
        "startup_stage": "mvp",
        "preferred_sectors": {"ai"},
        "preferred_stages": {"mvp"},
    }
    assert can_show_in_investor_feed(**args)
    assert not can_show_in_investor_feed(**{**args, "preferred_sectors": {"fintech"}})
    assert not can_show_in_investor_feed(**{**args, "preferred_stages": {"revenue"}})
    assert can_show_in_investor_feed(**{**args, "preferred_sectors": {"fintech"}, "show_all": True})
