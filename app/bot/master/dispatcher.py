import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.types import Update

from app.bot.master import onboarding, platform_admin
from app.bot.middlewares.db_session import DBSessionMiddleware
from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_master_dp: Dispatcher | None = None
_master_bot: Bot | None = None


async def get_master_bot() -> Bot:
    global _master_bot
    if _master_bot is None:
        _master_bot = Bot(
            token=settings.MASTER_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _master_bot


async def get_master_dispatcher() -> Dispatcher:
    global _master_dp
    if _master_dp is not None:
        return _master_dp

    redis = await get_redis()
    storage = RedisStorage(
        redis=redis,
        key_builder=DefaultKeyBuilder(prefix="masterlug_master"),
    )

    dp = Dispatcher(storage=storage)
    dp.update.outer_middleware(DBSessionMiddleware())
    platform_admin.register(dp)  # must be before onboarding — owner /start is caught here first
    onboarding.register(dp)

    _master_dp = dp
    logger.info("Master Dispatcher initialized.")
    return dp


async def process_master_update(update_data: dict) -> None:
    dp = await get_master_dispatcher()
    bot = await get_master_bot()
    update = Update(**update_data)
    try:
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("Unhandled error in master bot")
