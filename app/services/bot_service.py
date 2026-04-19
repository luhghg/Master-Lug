import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_token, hash_token
from app.models.bot import BotNiche, RegisteredBot

logger = logging.getLogger(__name__)


async def register_bot(
    session: AsyncSession,
    *,
    owner_telegram_id: int,
    plain_token: str,
    bot_username: str,
    niche: BotNiche = BotNiche.LABOR,
) -> RegisteredBot:
    """Register a new sub-bot. Stores hash for lookup + encrypted token for recovery."""
    bot = RegisteredBot(
        owner_telegram_id=owner_telegram_id,
        token_hash=hash_token(plain_token),
        encrypted_token=encrypt_token(plain_token),
        bot_username=bot_username,
        niche=niche,
    )
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    logger.info("Registered bot @%s (niche=%s)", bot_username, niche)
    return bot


async def get_bot_by_token(
    session: AsyncSession, plain_token: str
) -> RegisteredBot | None:
    """
    O(1) lookup by SHA-256 hash of the token.
    The plain token is NEVER stored in the DB.
    """
    result = await session.execute(
        select(RegisteredBot).where(
            RegisteredBot.token_hash == hash_token(plain_token),
            RegisteredBot.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()
