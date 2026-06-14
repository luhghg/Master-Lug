"""
Monobank Personal API webhook handler.
Receives incoming payment notifications and auto-activates bots.

Flow:
  Client pays → Monobank calls POST /monobank/{secret} → we parse comment
  → find bot by username → extend subscription → activate → notify owner
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from app.core.config import niche_price, settings
from app.core.database import AsyncSessionLocal
from app.models.bot import RegisteredBot
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()

# Monobank sends amounts in kopecks (UAH × 100)
_KOPECKS = 100

# Pattern: "MasterLug @some_bot_username" — case-insensitive
_COMMENT_RE = re.compile(r"masterlug\s+@([\w]+)", re.IGNORECASE)


class _StatementItem(BaseModel):
    id: str = ""
    amount: int = 0          # kopecks, positive = incoming to our account
    currencyCode: int = 0    # 980 = UAH
    comment: str = ""
    description: str = ""


class _EventData(BaseModel):
    account: str = ""
    statementItem: _StatementItem = _StatementItem()


class MonobankEvent(BaseModel):
    type: str = ""
    data: _EventData = _EventData()


async def _process_payment(item: _StatementItem) -> None:
    """Parse the transaction and activate the matching bot."""

    # Only incoming UAH payments
    if item.amount <= 0 or item.currencyCode != 980:
        return

    # Try to find "MasterLug @username" in comment or description
    text = item.comment or item.description
    match = _COMMENT_RE.search(text)
    if not match:
        # Regular personal transaction — silently ignore
        logger.debug("Monobank: ignoring transaction without MasterLug mention: %r", text)
        return

    username = match.group(1).lower()
    amount_uah = item.amount / _KOPECKS

    # Find bot first (needed for owner notification regardless of amount)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RegisteredBot).where(RegisteredBot.bot_username == username)
        )
        bot = result.scalar_one_or_none()

        if not bot:
            logger.warning("Monobank payment for unknown bot @%s", username)
            await _notify_owner_unknown(item)
            return

        expected = niche_price(bot.niche) * _KOPECKS
        if item.amount != expected:
            logger.info("Monobank wrong amount %d kopecks for @%s (expected %d)", item.amount, username, expected)
            await _notify_client_wrong_amount(bot.owner_telegram_id, username, amount_uah, niche_price(bot.niche))
            await _notify_owner_wrong_amount(item, username, amount_uah, owner_id=bot.owner_telegram_id, correct_price=niche_price(bot.niche))
            return

        # Extend subscription by exactly 1 month
        now = datetime.now(timezone.utc)
        base = max(bot.subscription_expires_at or now, now)
        bot.subscription_expires_at = base + timedelta(days=30)
        bot.is_active = True
        await session.commit()

        new_date = bot.subscription_expires_at.strftime("%d.%m.%Y")
        logger.info("Auto-activated @%s for 1 month → expires %s", username, new_date)

    # Notify bot owner
    await _notify_bot_owner(bot.owner_telegram_id, username, amount_uah, new_date)
    # Notify platform owner
    await _notify_platform_owner(username, bot.owner_telegram_id, amount_uah, new_date)


async def _notify_bot_owner(
    owner_id: int, username: str, amount_uah: float, new_date: str
) -> None:
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(
            chat_id=owner_id,
            text=(
                f"✅ <b>Оплату отримано! Бот активовано.</b>\n\n"
                f"🤖 @{username}\n"
                f"💰 Сума: <b>{amount_uah:.0f} грн</b> (1 місяць)\n"
                f"📅 Підписка активна до: <b>{new_date}</b>\n\n"
                f"Дякуємо! Бот вже працює для ваших клієнтів."
            ),
        )
    except Exception as e:
        logger.warning("Could not notify bot owner %s: %s", owner_id, e)


async def _notify_platform_owner(
    username: str, owner_id: int, amount_uah: float, new_date: str
) -> None:
    if not settings.PLATFORM_OWNER_ID:
        return
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(
            chat_id=settings.PLATFORM_OWNER_ID,
            text=(
                f"💰 <b>Нова оплата!</b>\n\n"
                f"🤖 @{username}\n"
                f"👤 Клієнт: <a href='tg://user?id={owner_id}'>{owner_id}</a>\n"
                f"💵 Сума: <b>{amount_uah:.0f} грн</b> (1 міс.)\n"
                f"📅 До: <b>{new_date}</b>\n\n"
                f"<i>Бот активовано автоматично.</i>"
            ),
        )
    except Exception as e:
        logger.warning("Could not notify platform owner: %s", e)


async def _notify_client_wrong_amount(owner_id: int, username: str, amount_uah: float, correct_price: int) -> None:
    """Notify the bot owner (client) that they sent the wrong amount."""
    support_hint = f"\n\n❓ Є питання? @{settings.SUPPORT_USERNAME}" if settings.SUPPORT_USERNAME else ""
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(
            chat_id=owner_id,
            text=(
                f"⚠️ <b>Ми отримали вашу оплату, але сума неправильна.</b>\n\n"
                f"💵 Ви надіслали: <b>{amount_uah:.0f} грн</b>\n"
                f"✅ Потрібно: <b>{correct_price} грн</b>\n\n"
                f"Бот <b>@{username}</b> ще не активовано.\n\n"
                f"Будь ласка, надішліть рівно <b>{correct_price} грн</b> з призначенням:\n"
                f"┌─────────────────────────┐\n"
                f"  <code>MasterLug @{username}</code>\n"
                f"└─────────────────────────┘"
                f"{support_hint}"
            ),
        )
    except Exception as e:
        logger.warning("Could not notify client about wrong amount %s: %s", owner_id, e)


async def _notify_owner_wrong_amount(item: _StatementItem, username: str, amount_uah: float, owner_id: int | None = None, correct_price: int | None = None) -> None:
    """Payment received but amount doesn't match expected subscription price."""
    if not settings.PLATFORM_OWNER_ID:
        return
    client_line = f"👤 Клієнт: <a href='tg://user?id={owner_id}'>{owner_id}</a>\n" if owner_id else ""
    expected_line = f"✅ Очікувалось: <b>{correct_price} грн</b>\n\n" if correct_price else ""
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(
            chat_id=settings.PLATFORM_OWNER_ID,
            text=(
                f"⚠️ <b>Неправильна сума оплати!</b>\n\n"
                f"🤖 @{username}\n"
                f"{client_line}"
                f"💵 Отримано: <b>{amount_uah:.0f} грн</b>\n"
                f"{expected_line}"
                f"<i>Бот НЕ активовано. Активуйте вручну через /admin якщо потрібно.</i>"
            ),
        )
    except Exception as e:
        logger.warning("Could not notify about wrong amount: %s", e)


async def _notify_owner_unknown(item: _StatementItem) -> None:
    """Payment received but we couldn't match it to a bot."""
    if not settings.PLATFORM_OWNER_ID:
        return
    amount_uah = item.amount / _KOPECKS
    try:
        from app.bot.master.dispatcher import get_master_bot
        bot = await get_master_bot()
        await bot.send_message(
            chat_id=settings.PLATFORM_OWNER_ID,
            text=(
                f"⚠️ <b>Невідома оплата!</b>\n\n"
                f"💵 Сума: <b>{amount_uah:.0f} грн</b>\n"
                f"📝 Призначення: <code>{item.comment or item.description or '—'}</code>\n\n"
                f"<i>Не вдалось автоматично визначити бота. Активуйте вручну через /admin.</i>"
            ),
        )
    except Exception as e:
        logger.warning("Could not notify about unknown payment: %s", e)


@router.post("/monobank/{secret}")
async def handle_monobank_webhook(
    secret: str = Path(...),
    event: MonobankEvent = ...,
) -> dict:
    if secret != settings.SECRET_WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    if event.type == "StatementItem":
        await _process_payment(event.data.statementItem)

    return {"status": "ok"}
