"""Slash-command menu registered with Telegram Bot API."""

from aiogram.types import BotCommand

PUBLIC_BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Запуск и меню RAUNDO"),
    BotCommand(command="rate", description="Оценивать стартапы"),
    BotCommand(command="startup", description="Мой стартап"),
    BotCommand(command="investor", description="Анкета инвестора"),
    BotCommand(command="top", description="Топ-10 за 14 дней"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="premium", description="Premium-подписки"),
    BotCommand(command="support", description="Поддержка"),
    BotCommand(command="complaint", description="Пожаловаться на объект"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="terms", description="Соглашение"),
    BotCommand(command="privacy", description="Политика данных"),
    BotCommand(command="legal", description="Все документы RAUNDO"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="cancel", description="Отменить текущий ввод"),
    BotCommand(command="paysupport", description="Поддержка по оплате"),
]
