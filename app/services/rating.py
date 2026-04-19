import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis
from app.models.user import User

logger = logging.getLogger(__name__)

RATING_CACHE_TTL = 300   # 5 minutes
MIN_RATING_THRESHOLD = 2.0


async def update_rating(
    session: AsyncSession, telegram_id: int, success: bool
) -> float:
    """
    Recalculate a user's global rating after a job outcome.
    Formula: weighted average — success = 5.0, fail = 1.0.
    Returns the new rating value.
    """
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.warning("update_rating called for unknown user %s", telegram_id)
        return 5.0

    if success:
        user.total_completed += 1
    else:
        user.total_failed += 1

    total = user.total_completed + user.total_failed
    user.global_rating = round(
        (user.total_completed * 5.0 + user.total_failed * 1.0) / total, 2
    )

    await session.commit()

    # Bust the cache so next read is fresh
    redis = await get_redis()
    await redis.delete(f"rating:{telegram_id}")

    logger.info(
        "Rating updated for user %s → %.2f (success=%s)",
        telegram_id, user.global_rating, success,
    )
    return user.global_rating


async def get_cached_rating(telegram_id: int, session: AsyncSession) -> float:
    """Return rating from Redis cache; fall back to DB on miss."""
    redis = await get_redis()
    cached = await redis.get(f"rating:{telegram_id}")
    if cached is not None:
        return float(cached)

    result = await session.execute(
        select(User.global_rating).where(User.telegram_id == telegram_id)
    )
    rating = result.scalar_one_or_none() or 5.0

    await redis.setex(f"rating:{telegram_id}", RATING_CACHE_TTL, str(rating))
    return rating


async def is_user_eligible(telegram_id: int, session: AsyncSession) -> bool:
    """Check whether a worker meets the minimum rating to accept jobs."""
    rating = await get_cached_rating(telegram_id, session)
    return rating >= MIN_RATING_THRESHOLD
