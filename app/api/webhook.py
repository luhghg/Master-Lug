import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app.bot.dispatcher import process_update
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models.bot import RegisteredBot
from app.services.bot_service import get_bot_by_token

logger = logging.getLogger(__name__)
router = APIRouter()

_SUB_WARN_DAYS = 7      # days before expiry to send first warning
_WARN_TTL_SEC  = 3 * 24 * 3600  # re-warn interval: 3 days


def _payment_instructions(bot_username: str) -> str:
    card = settings.MONOBANK_CARD or "—"
    price = settings.SUBSCRIPTION_PRICE
    master = settings.PLATFORM_OWNER_ID  # fallback; ideally use master bot username
    return (
        f"💳 <b>Monobank:</b> <code>{card}</code>\n"
        f"💰 Сума: <b>{price} грн/міс</b>\n"
        f"📝 Призначення: <code>MasterLug @{bot_username}</code>\n\n"
        f"Після оплати напишіть у @masterlugbot — активуємо вручну."
    )


async def _notify_owner(owner_id: int, text: str) -> None:
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(chat_id=owner_id, text=text)
    except Exception as e:
        logger.warning("Could not notify owner %s: %s", owner_id, e)


async def _check_subscription(registered_bot: RegisteredBot) -> bool:
    """Return True if bot is allowed to process updates. Side-effects: deactivate + notify."""
    if not registered_bot.is_active:
        return False

    exp = registered_bot.subscription_expires_at
    if exp is None:
        return True  # NULL = grandfathered / unlimited

    now = datetime.now(timezone.utc)

    if exp <= now:
        # Expired — deactivate and notify
        async with AsyncSessionLocal() as session:
            bot = await session.get(RegisteredBot, registered_bot.id)
            if bot and bot.is_active:
                bot.is_active = False
                await session.commit()

        await _notify_owner(
            registered_bot.owner_telegram_id,
            f"🔴 <b>Підписка на @{registered_bot.bot_username} закінчилась!</b>\n\n"
            f"Бот вимкнено. Щоб відновити:\n\n"
            + _payment_instructions(registered_bot.bot_username),
        )
        return False

    days_left = (exp - now).days
    if days_left <= _SUB_WARN_DAYS:
        redis = await get_redis()
        warn_key = f"sub_warn:{registered_bot.id}"
        if not await redis.exists(warn_key):
            await redis.setex(warn_key, _WARN_TTL_SEC, "1")
            await _notify_owner(
                registered_bot.owner_telegram_id,
                f"⚠️ <b>Підписка на @{registered_bot.bot_username} закінчується через {days_left} дн.</b>\n\n"
                f"Щоб не втратити бот:\n\n"
                + _payment_instructions(registered_bot.bot_username),
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
