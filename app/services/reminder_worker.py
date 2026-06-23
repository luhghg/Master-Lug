"""Background worker — sends pending appointment reminders every 60 seconds."""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, types as tg_types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decrypt_token
from app.models.appointment import (
    ApptBooking, ApptClient, ApptReminder, ReminderStatus, ReminderType,
)
from app.models.bot import RegisteredBot
from app.services.config_service import get_json

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


async def reminder_worker_loop() -> None:
    """Runs indefinitely, ticking every 60 s to dispatch pending reminders."""
    logger.info("Reminder worker started.")
    while True:
        try:
            await _run_once()
        except Exception:
            logger.exception("Reminder worker top-level error — continuing")
        await asyncio.sleep(60)
