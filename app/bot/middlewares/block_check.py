from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blocked_user import BotBlockedUser


class BlockCheckMiddleware(BaseMiddleware):
    """Reject any interaction from users blocked in this bot."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        bot_id: int | None = data.get("registered_bot_id")
        owner_id: int | None = data.get("owner_telegram_id")

        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if session and bot_id and user and user.id != owner_id:
            result = await session.execute(
                select(BotBlockedUser).where(
                    BotBlockedUser.bot_id == bot_id,
                    BotBlockedUser.telegram_id == user.id,
                )
            )
            if result.scalar_one_or_none():
                if isinstance(event, Message):
                    await event.answer("⛔ Ви заблоковані в цьому боті.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔ Ви заблоковані в цьому боті.", show_alert=True)
                return

        return await handler(event, data)
