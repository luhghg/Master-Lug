"""Background worker — sends pending appointment reminders and subscription warnings."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, types as tg_types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.security import decrypt_token
from app.models.appointment import (
    ApptBooking, ApptClient, ApptReminder, ReminderStatus, ReminderType,
)
from app.models.bot import BotNiche, RegisteredBot, SubscriptionReminder
from app.services.config_service import get_json

_TZ_KYIV = ZoneInfo("Europe/Kyiv")

logger = logging.getLogger(__name__)

_TTT_REMINDERS = "ttt_reminders"
_DAYS_UA = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

# Maps ReminderType → key in the TTT_REMINDERS JSON config
_RTYPE_CFG: dict[ReminderType, str] = {
    ReminderType.HOURS_168: "7d",
    ReminderType.HOURS_24:  "24h",
    ReminderType.HOURS_2:   "2h",
    ReminderType.REVIEW:    "review",
}


def _make_text(rtype: ReminderType, booking: ApptBooking) -> str:
    day = _DAYS_UA[booking.slot_date.weekday()]
    date_s = booking.slot_date.strftime("%d.%m.%Y")
    time_s = booking.slot_time
    style  = booking.style or "—"
    if rtype == ReminderType.HOURS_168:
        return (
            f"📅 <b>Нагадуємо!</b> Ваш сеанс через 7 днів.\n\n"
            f"📌 {day}, {date_s} о {time_s}\n"
            f"🎨 {style}"
        )
    if rtype == ReminderType.HOURS_24:
        return (
            f"⏰ <b>Нагадуємо!</b> Ваш сеанс завтра.\n\n"
            f"📌 {day}, {date_s} о {time_s}\n"
            f"🎨 {style}"
        )
    if rtype == ReminderType.HOURS_2:
        return (
            f"⚡ <b>Нагадуємо!</b> Ваш сеанс через 2 години!\n\n"
            f"📌 {day}, {date_s} о {time_s}\n"
            f"🎨 {style}"
        )
    # REVIEW
    return (
        "🎉 <b>Дякуємо за візит!</b>\n\n"
        "Якщо все пройшло чудово — будемо вдячні за відгук 🌟"
    )


async def _send_one(session, reminder: ApptReminder) -> None:
    now = datetime.now(timezone.utc)

    booking = await session.get(ApptBooking, reminder.booking_id)
    if not booking:
        reminder.status = ReminderStatus.SKIPPED
        return

    # Check master's per-bot reminder settings
    cfg_key = _RTYPE_CFG.get(reminder.reminder_type, "")
    if cfg_key:
        rems: dict = await get_json(session, booking.bot_id, _TTT_REMINDERS, {})
        if not rems.get(cfg_key, True):
            reminder.status = ReminderStatus.SKIPPED
            return

    client = await session.get(ApptClient, booking.client_id)
    if not client:
        reminder.status = ReminderStatus.SKIPPED
        return

    bot_record = await session.get(RegisteredBot, booking.bot_id)
    if not bot_record or not bot_record.is_active:
        reminder.status = ReminderStatus.SKIPPED
        return

    try:
        token = decrypt_token(bot_record.encrypted_token)
    except Exception as exc:
        logger.warning("Cannot decrypt token for bot %d: %s", booking.bot_id, exc)
        reminder.status = ReminderStatus.FAILED
        return

    kb = None
    if reminder.reminder_type == ReminderType.REVIEW:
        kb = tg_types.InlineKeyboardMarkup(inline_keyboard=[[
            tg_types.InlineKeyboardButton(
                text="⭐ Залишити відгук",
                callback_data=f"ttt_review:start:{booking.id}",
            ),
        ]])

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await bot.send_message(
            chat_id=client.telegram_id,
            text=_make_text(reminder.reminder_type, booking),
            reply_markup=kb,
        )
        reminder.status = ReminderStatus.SENT
        reminder.sent_at = now
    except TelegramForbiddenError:
        # Client blocked the bot — no point retrying
        reminder.status = ReminderStatus.FAILED
        logger.info(
            "Reminder %d: client %s blocked @%s",
            reminder.id, client.telegram_id, bot_record.bot_username,
        )
    except Exception as exc:
        reminder.status = ReminderStatus.FAILED
        logger.warning("Reminder %d failed to send: %s", reminder.id, exc)
    finally:
        await bot.session.close()


async def _run_once() -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ApptReminder)
            .where(
                ApptReminder.status == ReminderStatus.PENDING,
                ApptReminder.scheduled_at <= now,
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        )).scalars().all()

        if not rows:
            return

        logger.info("Reminder worker: processing %d due reminders", len(rows))
        for reminder in rows:
            try:
                await _send_one(session, reminder)
            except Exception:
                logger.exception("Unexpected error processing reminder %d", reminder.id)
                reminder.status = ReminderStatus.FAILED

        await session.commit()


async def _send_sub_warning(
    bot_rec: RegisteredBot, days_left: int, session
) -> bool:
    """Send a subscription warning to the bot owner. Returns True on success."""
    try:
        token = decrypt_token(bot_rec.encrypted_token)
    except Exception as exc:
        logger.warning("sub_warning: cannot decrypt token for bot %d: %s", bot_rec.id, exc)
        return False

    if days_left > 0:
        text = (
            f"⚠️ <b>Нагадування про підписку</b>\n\n"
            f"Ваша підписка закінчується через <b>{days_left} {'день' if days_left == 1 else 'дні' if days_left in (2, 3, 4) else 'днів'}</b>.\n\n"
            f"Продовжіть підписку, щоб бот продовжував працювати."
        )
    elif days_left == 0:
        text = (
            "🔴 <b>Підписка закінчилась сьогодні</b>\n\n"
            "Нові записи клієнтів заблоковано. У вас є 3 дні пільгового періоду.\n\n"
            "Будь ласка, продовжіть підписку якомога швидше."
        )
    else:
        grace_day = abs(days_left)
        text = (
            f"🔴 <b>Пільговий період: день {grace_day} з 3</b>\n\n"
            f"Підписка прострочена. Нові записи не приймаються.\n\n"
            f"Через {3 - grace_day} дн. бот буде деактивовано — продовжіть підписку зараз."
        )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(chat_id=bot_rec.owner_telegram_id, text=text)
        return True
    except TelegramForbiddenError:
        logger.info("sub_warning: owner %d blocked @%s", bot_rec.owner_telegram_id, bot_rec.bot_username)
        return False
    except Exception as exc:
        logger.warning("sub_warning: failed for bot %d: %s", bot_rec.id, exc)
        return False
    finally:
        await bot.session.close()


_last_sub_check_date: str = ""


async def _check_subscriptions() -> None:
    """Daily subscription check: send warnings and enforce grace period."""
    global _last_sub_check_date

    now_kyiv = datetime.now(_TZ_KYIV)
    today_str = now_kyiv.strftime("%Y-%m-%d")

    # Run once per day between 10:00 and 11:00 Kyiv
    if _last_sub_check_date == today_str or not (10 <= now_kyiv.hour < 11):
        return

    _last_sub_check_date = today_str
    now_utc = datetime.now(timezone.utc)
    today = now_kyiv.date()

    async with AsyncSessionLocal() as session:
        bots = (await session.execute(
            select(RegisteredBot).where(
                RegisteredBot.niche == BotNiche.TATTOO,
                RegisteredBot.subscription_expires_at.isnot(None),
            )
        )).scalars().all()

        for bot_rec in bots:
            expires = bot_rec.subscription_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            expires_date = expires.astimezone(_TZ_KYIV).date()
            days_left = (expires_date - today).days

            # Past grace period → deactivate
            if days_left < -3:
                if bot_rec.is_active:
                    bot_rec.is_active = False
                    logger.info("Bot %d (@%s) deactivated: subscription expired > 3 days ago", bot_rec.id, bot_rec.bot_username)
                continue

            # Pre-expiry warnings: 7, 3, 1 days before
            if days_left in (7, 3, 1):
                existing = (await session.execute(
                    select(SubscriptionReminder).where(
                        SubscriptionReminder.bot_id == bot_rec.id,
                        SubscriptionReminder.days_before == days_left,
                    )
                )).scalar_one_or_none()
                if not existing:
                    sent = await _send_sub_warning(bot_rec, days_left, session)
                    if sent:
                        session.add(SubscriptionReminder(bot_id=bot_rec.id, days_before=days_left))

            # Grace period (days_left in -3..-1) + expiry day (0)
            elif days_left <= 0:
                grace_key = days_left  # 0, -1, -2
                existing = (await session.execute(
                    select(SubscriptionReminder).where(
                        SubscriptionReminder.bot_id == bot_rec.id,
                        SubscriptionReminder.days_before == grace_key,
                    )
                )).scalar_one_or_none()
                if not existing:
                    sent = await _send_sub_warning(bot_rec, days_left, session)
                    if sent:
                        session.add(SubscriptionReminder(bot_id=bot_rec.id, days_before=grace_key))

        await session.commit()


async def reminder_worker_loop() -> None:
    """Runs indefinitely, ticking every 60 s to dispatch pending reminders."""
    logger.info("Reminder worker started.")
    while True:
        try:
            await _run_once()
        except Exception:
            logger.exception("Reminder worker top-level error — continuing")
        try:
            await _check_subscriptions()
        except Exception:
            logger.exception("Subscription check error — continuing")
        await asyncio.sleep(60)
