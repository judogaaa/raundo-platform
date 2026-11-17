"""Restricted /admin and /version. Full admin queues are Issue #8."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from raundo.config import get_settings
from raundo.keyboards import main_menu
from raundo.runtime_info import format_version_report
from raundo.texts import ADMIN_PLACEHOLDER


def _require_admin(message: Message) -> bool:
    actor_id = message.from_user.id if message.from_user else None
    return get_settings().is_admin(actor_id)


async def admin_message(message: Message) -> None:
    if not _require_admin(message):
        await message.answer("Доступ к административным функциям запрещён.")
        return
    await message.answer(ADMIN_PLACEHOLDER, reply_markup=main_menu())


async def version_message(message: Message) -> None:
    """Owner/dev-only: real process SHA/PID — never secrets or public command list."""
    if not _require_admin(message):
        await message.answer("Доступ к диагностике запрещён.")
        return
    await message.answer(format_version_report(), reply_markup=main_menu())


def create_router() -> Router:
    router = Router(name="admin")
    router.message.register(admin_message, Command("admin"))
    router.message.register(version_message, Command("version"))
    return router
