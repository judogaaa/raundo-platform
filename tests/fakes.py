"""Test doubles for Bot API session and Redis rate-limit counters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, User


class FakeRedis:
    """Minimal async Redis surface used by RateLimitMiddleware."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def aclose(self) -> None:
        return None


class RecordingSession(BaseSession):
    """Captures Bot API methods without network I/O."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        self.calls.append(method)
        api_method = method.__api_method__
        if api_method == "sendMessage":
            chat_id = getattr(method, "chat_id")
            return Message(  # type: ignore[return-value]
                message_id=len(self.calls),
                date=datetime.now(timezone.utc),
                chat=Chat(id=chat_id, type="private", first_name="Tester"),
                from_user=User(id=1, is_bot=True, first_name="RAUNDO"),
                text=getattr(method, "text", None),
            )
        if api_method in {"answerCallbackQuery", "setMyCommands"}:
            return True  # type: ignore[return-value]
        return True  # type: ignore[return-value]

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:  # pragma: no cover — required for async generator typing
            yield b""
        return


def sent_texts(session: RecordingSession) -> list[str]:
    texts: list[str] = []
    for call in session.calls:
        text = getattr(call, "text", None)
        if text and call.__api_method__ in {"sendMessage", "answerCallbackQuery"}:
            texts.append(str(text))
    return texts
