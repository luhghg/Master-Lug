import logging
import uuid
from datetime import datetime, timezone

from aiogram import Dispatcher, F, types
from aiogram.filters import CommandStart, CommandObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_state
from app.models.user import User
from app.services.job_service import get_job

logger = logging.getLogger(__name__)

TERMS_TEXT = (
    "👋 <b>Ласкаво просимо!</b>\n\n"
    "Перед початком роботи ознайомтесь з умовами:\n\n"
    "• Ми зберігаємо ваш Telegram ID, ім'я та username для роботи сервісу\n"
    "• Ваші дані не передаються третім особам\n"
    "• Роботодавець бачить ваше ім'я та @username при відгуку на вакансію\n"
    "• Ви можете видалити свої дані, написавши в підтримку\n\n"
    "Натискаючи <b>«Погоджуюсь»</b> ви приймаєте умови використання сервісу."
)


async def cmd_start(
    message: types.Message,
    command: CommandObject,
    session: AsyncSession,
    owner_telegram_id: int,
) -> None:
    user = await _get_user(session, message.from_user.id)

    # New user — show terms first, save deep-link arg for after consent
    if user is None or user.terms_agreed_at is None:
        arg = command.args or ""
        await message.answer(
            TERMS_TEXT,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✅ Погоджуюсь",
                            callback_data=f"consent:agree:{arg}",
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="❌ Відмовитись",
                            callback_data="consent:decline",
                        )
                    ],
                ]
            ),
        )
        return

    await _route(message, command.args, session, owner_telegram_id)


async def consent_agree(
    callback: types.CallbackQuery,
    session: AsyncSession,
    owner_telegram_id: int,
) -> None:
    arg = callback.data.split(":", 2)[2]  # consent:agree:<arg>

    # Create or update user with consent timestamp
    user = await _get_user(session, callback.from_user.id)
    if user is None:
        user = User(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
            terms_agreed_at=datetime.now(timezone.utc),
        )
        session.add(user)
    else:
        user.terms_agreed_at = datetime.now(timezone.utc)
    await session.commit()

    await callback.message.delete()
    await callback.answer()
    await _route(callback.message, arg or None, session, owner_telegram_id)


async def consent_decline(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(
        "❌ Ви відмовились від умов використання.\n\n"
        "Без згоди сервіс недоступний. Якщо передумаєте — натисніть /start."
    )
    await callback.answer()


# ── Routing after consent ─────────────────────────────────────────────────────

async def _route(
    message: types.Message,
    args: str | None,
    session: AsyncSession,
    owner_telegram_id: int,
) -> None:
    user_id = message.chat.id  # works for both Message and edited message

    if args and args.startswith("job_"):
        job_id_str = args[4:]
        try:
            job = await get_job(session, uuid.UUID(job_id_str))
            if job:
                await _show_job_card(message, job)
                return
        except ValueError:
            logger.warning("Invalid job UUID in deep link: %s", job_id_str)

    if user_id == owner_telegram_id:
        await _show_employer_panel(message)
    else:
        await _show_worker_panel(message)


# ── Panels ────────────────────────────────────────────────────────────────────

def _powered_by() -> str:
    if app_state.master_bot_username:
        return f"\n\n<i>Працює на платформі @{app_state.master_bot_username}</i>"
    return ""


async def _show_employer_panel(message: types.Message) -> None:
    await message.answer(
        f"👔 <b>Панель роботодавця</b>{_powered_by()}",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="➕ Нова вакансія",      callback_data="role:employer")],
                [types.InlineKeyboardButton(text="📋 Активні вакансії",  callback_data="employer:my_jobs"),
                 types.InlineKeyboardButton(text="📁 Архів",             callback_data="employer:archive")],
                [types.InlineKeyboardButton(text="👷 Мої працівники",    callback_data="employer:active_workers"),
                 types.InlineKeyboardButton(text="🚫 Заблоковані",       callback_data="employer:blocked")],
            ]
        ),
    )


async def _show_worker_panel(message: types.Message) -> None:
    await message.answer(
        f"👷 <b>Шукаєте роботу?</b>\n\n"
        f"Я допоможу знайти вакансії у вашому місті.{_powered_by()}",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🔍 Знайти роботу", callback_data="role:worker"
                    )
                ]
            ]
        ),
    )


async def _show_job_card(message: types.Message, job) -> None:
    await message.answer(
        f"📋 <b>Вакансія</b>\n\n"
        f"📍 Місто: {job.city}\n"
        f"💰 Оплата: {job.pay_description}\n"
        f"⏰ Час: {job.scheduled_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"📌 Адреса: {job.location}\n\n"
        f"📝 {job.description}",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Відгукнутись", callback_data=f"apply:{job.id}"
                    )
                ]
            ]
        ),
    )


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, CommandStart())
    dp.callback_query.register(consent_agree, F.data.startswith("consent:agree:"))
    dp.callback_query.register(consent_decline, F.data == "consent:decline")
