import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app.bot.dispatcher import process_update
from app.core.config import niche_price, settings
from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models.bot import RegisteredBot
from app.services.bot_service import get_bot_by_token

logger = logging.getLogger(__name__)
router = APIRouter()

# Warning thresholds (days before expiry) and their Redis TTLs (seconds)
# Each threshold fires exactly once and never repeats for that subscription period
_WARN_LEVELS = [
    (7, 8 * 24 * 3600),   # 7-day warning, key lives 8 days
    (3, 4 * 24 * 3600),   # 3-day strong warning
    (2, 3 * 24 * 3600),   # 2-day strong warning
    (1, 2 * 24 * 3600),   # 1-day critical warning
]


def _support_line() -> str:
    if settings.SUPPORT_USERNAME:
        return f"\n\n❓ Виникли питання? @{settings.SUPPORT_USERNAME}"
    return ""


def _payment_block(bot_username: str, niche=None) -> str:
    card = settings.MONOBANK_CARD or "—"
    price = niche_price(niche) if niche is not None else settings.SUBSCRIPTION_PRICE
    return (
        f"💳 <b>Monobank:</b> <code>{card}</code>\n"
        f"💰 Сума: <b>{price} грн/міс</b>\n\n"
        f"⚠️ <b>ОБОВ'ЯЗКОВО вкажіть призначення платежу:</b>\n"
        f"┌─────────────────────────┐\n"
        f"  <code>MasterLug @{bot_username}</code>\n"
        f"└─────────────────────────┘\n"
        f"👆 <b>Просто скопіюйте та вставте цей текст при переказі!</b>\n"
        f"<i>Без правильного призначення ми не зможемо знайти вашу оплату.</i>\n\n"
        f"✅ Бот активується <b>автоматично</b> одразу після оплати."
        + _support_line()
    )


def _warn_text(bot_username: str, days_left: int, niche=None) -> str:
    if days_left == 1:
        return (
            f"🚨 <b>УВАГА! Бот @{bot_username} вимкнеться ЗАВТРА!</b>\n\n"
            f"Залишився <b>1 день</b> підписки.\n"
            f"Якщо не оплатити сьогодні — бот перестане відповідати клієнтам!\n\n"
            f"⬇️ Оплатіть прямо зараз:\n\n"
            + _payment_block(bot_username, niche)
        )
    if days_left == 2:
        return (
            f"🚨 <b>УВАГА! До вимкнення бота @{bot_username} — 2 дні!</b>\n\n"
            f"Без оплати бот вимкнеться і клієнти не зможуть ним користуватись.\n\n"
            f"⬇️ Оплатіть щоб уникнути перерви в роботі:\n\n"
            + _payment_block(bot_username, niche)
        )
    if days_left == 3:
        return (
            f"⚠️ <b>Підписка на @{bot_username} закінчується через 3 дні!</b>\n\n"
            f"Не забудьте поновити — без оплати бот вимкнеться.\n\n"
            + _payment_block(bot_username, niche)
        )
    return (
        f"⚠️ <b>Підписка на @{bot_username} закінчується через {days_left} дн.</b>\n\n"
        f"Оплатіть заздалегідь щоб не переривати роботу бота.\n\n"
        + _payment_block(bot_username, niche)
    )


async def _notify_owner(owner_id: int, text: str) -> None:
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(chat_id=owner_id, text=text)
    except Exception as e:
        logger.warning("Could not notify owner %s: %s", owner_id, e)


async def _check_subscription(registered_bot: RegisteredBot) -> bool:
    """Return True if bot may process updates. Handles deactivation + tiered warnings."""
    # Platform owner's bots are always active — no subscription checks
    if settings.PLATFORM_OWNER_ID and registered_bot.owner_telegram_id == settings.PLATFORM_OWNER_ID:
        return True

    if not registered_bot.is_active:
        return False

    exp = registered_bot.subscription_expires_at
    if exp is None:
        return True  # NULL = grandfathered / unlimited

    now = datetime.now(timezone.utc)

    if exp <= now:
        async with AsyncSessionLocal() as session:
            bot = await session.get(RegisteredBot, registered_bot.id)
            if bot and bot.is_active:
                bot.is_active = False
                await session.commit()

        await _notify_owner(
            registered_bot.owner_telegram_id,
            f"🔴 <b>Підписка на @{registered_bot.bot_username} закінчилась!</b>\n\n"
            f"Бот вимкнено. Клієнти не можуть ним користуватись.\n\n"
            f"Щоб відновити роботу — оплатіть підписку:\n\n"
            + _payment_block(registered_bot.bot_username, registered_bot.niche),
        )
        return False

    days_left = (exp - now).days
    redis = await get_redis()

    # Fire warning for each threshold exactly once
    for threshold, ttl in _WARN_LEVELS:
        if days_left <= threshold:
            warn_key = f"sub_warn:{registered_bot.id}:{threshold}"
            if not await redis.exists(warn_key):
                await redis.setex(warn_key, ttl, "1")
                await _notify_owner(
                    registered_bot.owner_telegram_id,
                    _warn_text(registered_bot.bot_username, days_left, registered_bot.niche),
                )

    return True


@router.post("/webhook/{bot_token}")
async def handle_webhook(
    bot_token: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if x_telegram_bot_api_secret_token != settings.SECRET_WEBHOOK_TOKEN:
        logger.warning("Rejected webhook: invalid secret token")
        raise HTTPException(status_code=403, detail="Forbidden")

    async with AsyncSessionLocal() as session:
        registered_bot = await get_bot_by_token(session, bot_token)

    if not registered_bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    if not await _check_subscription(registered_bot):
        return {"status": "ok"}  # silent 200 so Telegram doesn't retry

    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    await process_update(
        plain_token=bot_token,
        registered_bot_id=registered_bot.id,
        bot_username=registered_bot.bot_username,
        owner_telegram_id=registered_bot.owner_telegram_id,
        bot_niche=registered_bot.niche.value,
        update_data=update_data,
    )

    return {"status": "ok"}
