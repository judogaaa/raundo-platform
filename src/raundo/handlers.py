"""Compatibility exports for tests. Prefer raundo.routers / raundo.app for new code."""

from raundo.routers import build_root_router
from raundo.routers.admin import admin_message

# Lazy single instance for filter unit tests; feed_update tests use setup_dispatcher().
router = build_root_router()

__all__ = ["admin_message", "router"]
