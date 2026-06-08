import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encrypt_token, hash_token
from app.models.bot import BotNiche, RegisteredBot

TRIAL_DAYS  = 30
TRIAL_LIMIT = 3   # first N unique clients get their first bot free

logger = logging.getLogger(__name__)


async def register_bot(
    session: AsyncSession,
    *,
    owner_telegram_id: int,
    plain_token: str,
    bot_username: str,
    niche: BotNiche = BotNiche.LABOR,
    referred_by: int | None = None,
) -> tuple[RegisteredBot, bool]:
    """Register a new sub-bot. Returns (bot, is_trial).

    Trial rules:
    - Only the FIRST bot per owner is eligible
    - Only if the platform has fewer than TRIAL_LIMIT unique clients so far
    """
    from app.core.config import settings

    demo_ids = {settings.DEMO_BOT_LABOR_ID, settings.DEMO_BOT_BEAUTY_ID} - {0}

    # How many bots does this owner already have?
    owner_q = select(func.count(RegisteredBot.id)).where(
        RegisteredBot.owner_telegram_id == owner_telegram_id
    )
    if demo_ids:
        owner_q = owner_q.where(RegisteredBot.id.not_in(demo_ids))
    owner_bot_count = await session.scalar(owner_q) or 0

    # How many distinct clients are on the platform so far?
    clients_q = select(func.count(func.distinct(RegisteredBot.owner_telegram_id)))
    if demo_ids:
        clients_q = clients_q.where(RegisteredBot.id.not_in(demo_ids))
    client_count = await session.scalar(clients_q) or 0

    # Trial only for first TRIAL_LIMIT clients, and only their FIRST bot
    is_trial = owner_bot_count == 0 and client_count < TRIAL_LIMIT

    bot = RegisteredBot(
        owner_telegram_id=owner_telegram_id,
        token_hash=hash_token(plain_token),
        encrypted_token=encrypt_token(plain_token),
        bot_username=bot_username,
        niche=niche,
        is_active=is_trial,
        subscription_expires_at=(
            datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS) if is_trial else None
        ),
        referred_by=referred_by,
    )
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    logger.info("Registered bot @%s (niche=%s, trial=%s)", bot_username, niche, is_trial)
    return bot, is_trial


async def get_bot_by_token(
    session: AsyncSession, plain_token: str
) -> RegisteredBot | None:
    result = await session.execute(
        select(RegisteredBot).where(
            RegisteredBot.token_hash == hash_token(plain_token),
        )
    )
    return result.scalar_one_or_none()
