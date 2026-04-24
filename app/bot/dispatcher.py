"""
Bot registry & dispatcher factory.

Architecture decision: ONE shared Dispatcher, MANY Bot instances.
- FSM states are namespaced per (bot_id, user_id) via DefaultKeyBuilder.
- Bot-specific data (registered_bot_id, bot_username) is injected per request
  via dp.feed_update(**kwargs) and becomes available as handler parameters.
"""

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.types import Update

from app.bot.handlers import employer, start, worker
from app.bot.handlers.niche.beauty import admin as beauty_admin
from app.bot.handlers.niche.beauty import client as beauty_client
from app.bot.middlewares.block_check import BlockCheckMiddleware
from app.bot.middlewares.db_session import DBSessionMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# One dispatcher shared by all sub-bots
_dispatcher: Dispatcher | None = None

# token → Bot instance cache
_bot_cache: dict[str, Bot] = {}


async def _get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is not None:
        return _dispatcher

    redis = await get_redis()
    storage = RedisStorage(
        redis=redis,
        key_builder=DefaultKeyBuilder(prefix="arete_fsm", with_bot_id=True),
    )

    dp = Dispatcher(storage=storage)

    # Middlewares — order matters
    dp.update.outer_middleware(DBSessionMiddleware())
    dp.message.middleware(BlockCheckMiddleware())
    dp.callback_query.middleware(BlockCheckMiddleware())
    dp.message.middleware(RateLimitMiddleware())

    # Handlers
    start.register(dp)
    employer.register(dp)
    worker.register(dp)
    beauty_client.register(dp)
    beauty_admin.register(dp)

    _dispatcher = dp
    logger.info("Shared Dispatcher initialized.")
    return dp


def _get_or_create_bot(token: str) -> Bot:
    if token not in _bot_cache:
        _bot_cache[token] = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        logger.info("Created Bot instance (total active: %d)", len(_bot_cache))
    return _bot_cache[token]


async def process_update(
    plain_token: str,
    registered_bot_id: int,
    bot_username: str,
    owner_telegram_id: int,
    bot_niche: str,
    update_data: dict,
) -> None:
    """
    Entry point called by the webhook endpoint.
    Errors are caught here so one bad update never crashes the server.
    """
    try:
        dp = await _get_dispatcher()
        bot = _get_or_create_bot(plain_token)
        update = Update(**update_data)
        await dp.feed_update(
            bot,
            update,
            registered_bot_id=registered_bot_id,
            bot_username=bot_username,
            owner_telegram_id=owner_telegram_id,
            bot_niche=bot_niche,
        )
    except Exception:
        logger.exception(
            "Unhandled exception while processing update for @%s", bot_username
        )
