"""Telegram-native card renderers (no Mini App)."""

from raundo.cards.investor_card import (
    InvestorCardView,
    build_investor_card_view,
    compact_investor_html,
    details_investor_html,
)
from raundo.cards.startup_card import (
    StartupCardView,
    build_startup_card_view,
    compact_startup_html,
    details_startup_html,
)

__all__ = [
    "InvestorCardView",
    "StartupCardView",
    "build_investor_card_view",
    "build_startup_card_view",
    "compact_investor_html",
    "compact_startup_html",
    "details_investor_html",
    "details_startup_html",
]
