"""User-facing copy must not leak internals or em/en dashes."""

from pathlib import Path

from raundo import premium_texts, texts, wizard_copy
from raundo.premium_catalog import STATUS_LEGEND
from raundo.ui_labels import contact_policy_label, review_state_label

_BANNED_TOKENS = (
    "Issue #",
    "grandfathered_gallery",
    "startup_id",
    "Verified label",
    "minor RUB",
    "{'scope'",
)
_DASHES = ("—", "–")
_MODULES = (texts, wizard_copy, premium_texts)


def test_user_copy_modules_have_no_emdash_or_issue_markers() -> None:
    for module in _MODULES:
        path = Path(module.__file__)
        content = path.read_text(encoding="utf-8")
        for dash in _DASHES:
            assert dash not in content, f"{path.name} contains forbidden dash"
        for token in _BANNED_TOKENS:
            assert token not in content, f"{path.name} contains {token}"


def test_status_legend_is_honest_without_github_issues() -> None:
    assert "Issue #" not in STATUS_LEGEND
    for dash in _DASHES:
        assert dash not in STATUS_LEGEND


def test_review_and_contact_labels() -> None:
    assert review_state_label("submitted") == "На проверке"
    assert review_state_label("draft") == "Черновик"
    assert contact_policy_label("relay") == "Через RAUNDO после подтверждения запроса"
    assert "username" not in contact_policy_label("username")
