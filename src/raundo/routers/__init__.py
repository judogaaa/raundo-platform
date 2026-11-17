"""Compose application routers."""

from aiogram import F, Router

from raundo.routers import (
    admin,
    common,
    complaints,
    feed,
    investor,
    premium,
    profile,
    stars_refunds,
    startup,
    support,
)


def build_root_router() -> Router:
    root = Router(name="raundo_root")
    # Entire product UI stays in private chats, including /admin.
    root.message.filter(F.chat.type == "private")
    root.callback_query.filter(F.message.chat.type == "private")
    # Admin before common catch-all so /admin is never swallowed.
    root.include_router(admin.create_router())
    root.include_router(stars_refunds.create_router())
    # Profile wizards before common catch-alls.
    root.include_router(startup.create_router())
    root.include_router(investor.create_router())
    # Premium before common catch-alls (⭐ Premium button + /premium).
    root.include_router(premium.create_router())
    root.include_router(support.create_router())
    root.include_router(complaints.create_router())
    # Feed/profile before common catch-alls: these used to be placeholders.
    root.include_router(feed.create_router())
    root.include_router(profile.create_router())
    root.include_router(common.create_router())
    return root
