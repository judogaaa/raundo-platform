"""Bot-wide UX contract: menu, labels, copy hygiene, outbox wording.

These checks are intentionally structural: they fail when a screen starts showing
internal codes, an em dash slips back into Russian copy, a router drifts from the
shared menu label, or two status maps disagree again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from raundo import ui
from raundo.keyboards import (
    MENU_FAVORITES,
    MENU_INVESTOR,
    MENU_PREMIUM,
    MENU_PROFILE,
    MENU_RATE,
    MENU_REQUESTS,
    MENU_ROWS,
    MENU_SETTINGS,
    MENU_STARTUP,
    MENU_SUPPORT,
    MENU_TOP,
)
from raundo.services.outbox_worker import render_message
from raundo.ui_labels import review_state_label

ROOT = Path(__file__).resolve().parents[1] / "src" / "raundo"

USER_FACING = [
    ROOT / "routers",
    ROOT / "wizard_copy.py",
    ROOT / "premium_texts.py",
    ROOT / "premium_catalog.py",
    ROOT / "keyboards.py",
    ROOT / "keyboard_grid.py",
    ROOT / "services" / "outbox_worker.py",
    ROOT / "services" / "contact_requests.py",
    ROOT / "services" / "complaints.py",
]

BANNED = (
    "None",
    "null",
    "undefined",
    "Traceback",
    "startup_id",
    "organization_pro",
    "pending_changes",
    "changes_requested",
    "not_yet",
    "quota_exceeded",
    "этапе подключения функций",
    "Безлимит",
    "безлимит",
)

CYR = re.compile(r"[А-Яа-яЁё]")


def _files() -> list[Path]:
    out: list[Path] = []
    for target in USER_FACING:
        if target.is_dir():
            out.extend(sorted(target.glob("*.py")))
        elif target.exists():
            out.append(target)
    return out


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _user_strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            if node.value.strip():
                yield node.lineno, node.value


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_user_facing_strings_have_no_internal_codes_or_dashes(path: Path) -> None:
    problems: list[str] = []
    for lineno, value in _user_strings(path):
        if not CYR.search(value):
            continue
        for token in BANNED:
            if token in value:
                problems.append(f"{path.name}:{lineno}: {token} in {value[:80]!r}")
        for dash in ("—", "–"):
            if dash in value:
                problems.append(f"{path.name}:{lineno}: em/en dash in {value[:80]!r}")
    assert not problems, "\n".join(problems)


def test_main_menu_is_compact_and_has_no_duplicates() -> None:
    labels = [label for row in MENU_ROWS for label in row]
    assert len(labels) == len(set(labels)) == 10
    assert len(MENU_ROWS) == 5
    assert all(len(row) == 2 for row in MENU_ROWS)


def test_approved_feed_keyboard_is_unchanged() -> None:
    from raundo.routers.feed import _feed_keyboard

    rows = [[button.text for button in row] for row in _feed_keyboard().keyboard]
    assert rows == [
        ["❤️", "👎"],
        ["⭐", MENU_TOP],
        ["Подробнее"],
        ["🤝 Запрос на знакомство"],
    ]
    flat = [label for row in rows for label in row]
    assert len(flat) == 6
    assert "Пропустить" not in flat


def test_menu_labels_are_shared_with_routers() -> None:
    from raundo.routers import feed, investor, profile, startup, support

    assert support.MENU_SUPPORT == MENU_SUPPORT
    assert feed.MENU_RATE == MENU_RATE
    assert feed.MENU_TOP == MENU_TOP
    assert profile.MENU_PROFILE == MENU_PROFILE
    assert profile.MENU_SETTINGS == MENU_SETTINGS
    assert profile.MENU_REQUESTS == MENU_REQUESTS
    assert profile.MENU_FAVORITES == MENU_FAVORITES
    assert startup.MENU_STARTUP == MENU_STARTUP
    assert investor.MENU_INVESTOR == MENU_INVESTOR
    assert {MENU_RATE, MENU_TOP, MENU_STARTUP, MENU_INVESTOR, MENU_PROFILE,
            MENU_PREMIUM, MENU_FAVORITES, MENU_REQUESTS, MENU_SUPPORT,
            MENU_SETTINGS} == {label for row in MENU_ROWS for label in row}


def test_review_status_has_one_wording_everywhere() -> None:
    codes = (
        "draft",
        "submitted",
        "under_review",
        "approved",
        "changes_requested",
        "pending_changes",
        "rejected",
        "archived",
        "suspended",
        "claimed",
        "revoked",
    )
    for code in codes:
        assert ui.status(code) == review_state_label(code)
        assert code not in ui.status(code)
    assert review_state_label(None) == "Черновик"


def test_plan_titles_are_human() -> None:
    assert ui.plan_title("organization_pro") == "🏢 Organization PRO"
    assert "organization_pro" not in ui.plan_title("organization_pro")
    assert ui.plan_title("unknown") == "Premium"


def test_ticket_and_request_statuses_are_human() -> None:
    assert ui.ticket_status("waiting_user") == "Ожидает уточнения"
    assert ui.ticket_status("awaiting_confirmation") == "Ожидает подтверждения"
    assert ui.ticket_status("needs_help") == "Требуется помощь"
    assert ui.ticket_status("in_progress") == "В работе"
    assert "waiting_user" not in ui.ticket_status("waiting_user")
    assert ui.request_status("pending") == "ждёт ответа"
    for raw in ("pending", "accepted", "declined"):
        assert raw not in ui.request_status(raw)


@pytest.mark.parametrize(
    "kind",
    [
        "startup.approved",
        "startup.changes_requested",
        "startup.rejected",
        "investor.approved",
        "organization.approved",
        "membership.approved",
        "membership.revoked",
        "support.reply",
        "user.restricted",
        "complaint.resolved",
        "complaint.dismissed",
        "contact.requested",
        "contact.accepted",
        "contact.declined",
    ],
)
def test_outbox_copy_is_human_and_never_empty(kind: str) -> None:
    event = SimpleNamespace(kind=kind, safe_payload={})
    text = render_message(event)
    assert text.strip()
    assert not text.rstrip().endswith(":")
    for token in ("None", "null", "startup_id", "event_type", "<b>", "</b>"):
        assert token not in text
