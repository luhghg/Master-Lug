import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """
    Sliding-window rate limiter backed by Redis.
    Blocks users who send more than RATE_LIMIT_REQUESTS messages
    within RATE_LIMIT_WINDOW seconds.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        redis = await get_redis()
        key = f"rl:{user_id}"

        count = await redis.incr(key)
        if count == 1:
            # First message in window — set TTL
            await redis.expire(key, settings.RATE_LIMIT_WINDOW)

        if count > settings.RATE_LIMIT_REQUESTS:
            logger.warning("Rate limit hit: user_id=%s count=%s", user_id, count)
            await event.answer(
                "⚠️ Забагато запитів. Зачекайте трохи і спробуйте знову."
            )
            return None

        return await handler(event, data)
