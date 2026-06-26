"""Master/admin handlers for the TATTOO niche — booking management, schedule, clients."""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types

_TZ = ZoneInfo("Europe/Kyiv")
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import (
    ApptBlockedDate, ApptBooking, ApptBookingStatus,
    ApptClient, ApptDeposit, ApptDepositStatus, ApptSchedule, ApptScheduleOverride,
)
from app.models.tattoo import TattooPortfolio, TattooReview, ReviewStatus, TattooService
from app.services.config_service import get_cfg, get_json
from app.bot.handlers.niche.tattoo.wizard import TTT_STYLES

logger = logging.getLogger(__name__)

_SOCIAL_TEXT    = "ttt_social"
_DEFAULT_SOCIAL = "📱 Сторінки майстра поки не налаштовані."
_IG_RE          = re.compile(r"@([\w.]+)")


def _contact_line(social: str) -> str:
    """Return a contact phrase if an Instagram handle is present; empty string otherwise."""
    if not social or social == _DEFAULT_SOCIAL:
        return ""
    m = _IG_RE.search(social)
    if m:
        return f"Якщо щось зміниться — напишіть нам в Instagram: @{m.group(1)} заздалегідь."
    return ""

_DAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]
_DAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


# ── FSM ───────────────────────────────────────────────────────────────────────

class TattooMasterFSM(StatesGroup):
    portfolio_photo = State()
    portfolio_style = State()
    portfolio_desc  = State()
    portfolio_time  = State()
    portfolio_price = State()
    cancel_reason   = State()
    client_note     = State()
    block_date_start = State()
    block_date_end   = State()
    block_reason     = State()
    deposit_card         = State()
    deposit_amount       = State()
    welcome_text         = State()
    sched_ovr_slot_input = State()
    sched_flex_time_input = State()


# ── Admin menu ────────────────────────────────────────────────────────────────

def _admin_markup() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📋 Записи",    callback_data="tttm_records"),
            types.InlineKeyboardButton(text="👥 Клієнти",   callback_data="tttm_clients"),
        ],
        [
            types.InlineKeyboardButton(text="🗓 Розклад",   callback_data="tttm_schedule"),
            types.InlineKeyboardButton(text="🚫 Відпустка", callback_data="tttm_blocked"),
        ],
        [
            types.InlineKeyboardButton(text="🎨 Портфоліо", callback_data="tttm_portfolio"),
            types.InlineKeyboardButton(text="⚙️ Налаштування", callback_data="tttm_settings"),
        ],
        [
            types.InlineKeyboardButton(text="📖 Довідка",   callback_data="tttm_help:menu"),
        ],
    ])


async def show_admin_menu(message: types.Message) -> None:
    await message.answer(
        "⚙️ <b>Панель майстра</b>\n\nОберіть розділ:",
        reply_markup=_admin_markup(),
    )


# ── Booking list ──────────────────────────────────────────────────────────────

_STATUS_FILTERS = {
    "pending":   [ApptBookingStatus.PENDING, ApptBookingStatus.AWAITING_DEPOSIT],
    "upcoming":  [ApptBookingStatus.CONFIRMED],
    "completed": [ApptBookingStatus.COMPLETED],
    "cancelled": [ApptBookingStatus.CANCELLED_BY_CLIENT, ApptBookingStatus.CANCELLED_BY_MASTER,
                  ApptBookingStatus.NO_SHOW],
}
_TAB_NAMES = {
    "pending":   "⏳ Очікують",
    "upcoming":  "✅ Підтверджені",
    "completed": "📁 Архів",
}
_STATUS_LABELS = {
    ApptBookingStatus.PENDING:             "⏳ Нова анкета",
    ApptBookingStatus.AWAITING_DEPOSIT:    "💳 Очікує депозит",
    ApptBookingStatus.CONFIRMED:           "✅ Підтверджено",
    ApptBookingStatus.COMPLETED:           "✔️ Завершено",
    ApptBookingStatus.CANCELLED_BY_CLIENT: "❌ Скасовано клієнтом",
    ApptBookingStatus.CANCELLED_BY_MASTER: "❌ Відхилено майстром",
    ApptBookingStatus.NO_SHOW:             "👻 No-show",
    ApptBookingStatus.RESCHEDULED:         "🔄 Перенесено",
}


def _tabs_row() -> list[types.InlineKeyboardButton]:
    return [
        types.InlineKeyboardButton(text="⏳ Очікують",    callback_data="tttm_list:pending"),
        types.InlineKeyboardButton(text="✅ Підтверджені", callback_data="tttm_list:upcoming"),
        types.InlineKeyboardButton(text="📁 Архів",        callback_data="tttm_list:completed"),
    ]


async def admin_records_home(callback: types.CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.edit_text(
            "📋 <b>Записи</b>\n\nОберіть вкладку:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                _tabs_row(),
                [types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")],
            ]),
        )
    except Exception:
        pass


async def admin_list(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    tab = callback.data.split(":")[1]
    statuses = _STATUS_FILTERS.get(tab, _STATUS_FILTERS["pending"])
    tab_label = _TAB_NAMES.get(tab, tab)

    rows = (await session.execute(
        select(ApptBooking)
        .where(
            ApptBooking.bot_id == registered_bot_id,
            ApptBooking.status.in_(statuses),
        )
        .order_by(ApptBooking.slot_date, ApptBooking.slot_time)
        .limit(20)
    )).scalars().all()

    if not rows:
        try:
            await callback.message.edit_text(
                f"📋 <b>{tab_label}</b>\n\nЗаписів немає.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    _tabs_row(),
                    [types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")],
                ]),
            )
        except Exception:
            pass
        await callback.answer()
        return

    bk_rows = [
        [types.InlineKeyboardButton(
            text=(
                f"{_STATUS_LABELS.get(b.status, '?')} | "
                f"{b.slot_date.strftime('%d.%m')} {b.slot_time} | "
                f"#{b.id}"
            ),
            callback_data=f"tttm_bk:{b.id}:view",
        )]
        for b in rows
    ]
    bk_rows.append(_tabs_row())
    bk_rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])

    try:
        await callback.message.edit_text(
            f"📋 <b>{tab_label}:</b>",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=bk_rows),
        )
    except Exception:
        await callback.message.answer(
            f"📋 <b>{tab_label}:</b>",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=bk_rows),
        )
    await callback.answer()


# ── Single booking ────────────────────────────────────────────────────────────

async def booking_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    bot: Bot,
) -> None:
    booking_id = int(callback.data.split(":")[1])
    booking = await session.get(ApptBooking, booking_id)
    if not booking or booking.bot_id != registered_bot_id:
        await callback.answer("Запис не знайдено.", show_alert=True)
        return

    client = await session.get(ApptClient, booking.client_id)
    deposit = (await session.execute(
        select(ApptDeposit).where(ApptDeposit.booking_id == booking_id)
    )).scalar_one_or_none()

    mention = ""
    if client:
        mention = f"@{client.username}" if client.username else (client.full_name or f"ID {client.telegram_id}")

    day_ua = _DAYS_SHORT[booking.slot_date.weekday()]
    allergy = booking.allergy_text or "Немає"
    overlap = booking.overlap_text or "Немає"
    ref_line = "📎 Є фото (у попередніх повідомленнях)" if booking.reference_file_id else "—"
    dep_line = "—"
    if deposit:
        dep_line = (
            f"{deposit.amount} грн / "
            + {
                ApptDepositStatus.WAITING:         "⏳ очікуємо",
                ApptDepositStatus.SCREENSHOT_SENT: "📸 скріншот надісланий",
                ApptDepositStatus.CONFIRMED:       "✅ підтверджено",
                ApptDepositStatus.RETURNED:        "↩️ повернуто",
                ApptDepositStatus.KEPT:            "🔒 залишено майстру",
            }.get(deposit.status, str(deposit.status))
        )

    text = (
        f"<b>Запис #{booking.id}</b>\n"
        f"Статус: {_STATUS_LABELS.get(booking.status, str(booking.status))}\n\n"
        f"👤 {mention}\n"
        f"🎨 {booking.style or '—'}\n"
        f"📍 {booking.body_zone or '—'}, {booking.body_size or '—'}\n"
        f"📎 Референс: {ref_line}\n"
        f"💊 Алергія: {allergy}\n"
        f"♻️ Перекриття: {overlap}\n\n"
        f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n"
        f"💳 Депозит: {dep_line}"
    )

    kb_rows = []
    if booking.status == ApptBookingStatus.AWAITING_DEPOSIT:
        kb_rows.append([
            types.InlineKeyboardButton(
                text="✅ Підтвердити депозит",
                callback_data=f"tttm_bk:{booking_id}:confirm_deposit",
            ),
        ])
        kb_rows.append([
            types.InlineKeyboardButton(
                text="❌ Відхилити",
                callback_data=f"tttm_bk:{booking_id}:cancel_pre",
            ),
        ])
    if booking.status == ApptBookingStatus.CONFIRMED:
        kb_rows.append([
            types.InlineKeyboardButton(
                text="✔️ Завершити сеанс",
                callback_data=f"tttm_bk:{booking_id}:complete",
            ),
            types.InlineKeyboardButton(
                text="👻 No-show",
                callback_data=f"tttm_bk:{booking_id}:noshow",
            ),
        ])
        kb_rows.append([
            types.InlineKeyboardButton(
                text="❌ Скасувати",
                callback_data=f"tttm_bk:{booking_id}:cancel_pre",
            ),
        ])
    if booking.status == ApptBookingStatus.PENDING:
        kb_rows.append([
            types.InlineKeyboardButton(
                text="❌ Відхилити",
                callback_data=f"tttm_bk:{booking_id}:cancel_pre",
            ),
        ])

    kb_rows.append([types.InlineKeyboardButton(text="◀️ До списку", callback_data="tttm_list:pending")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await callback.answer()


async def booking_action(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    bot: Bot,
    state: FSMContext,
) -> None:
    parts = callback.data.split(":")
    booking_id = int(parts[1])
    action = parts[2]

    booking = await session.get(ApptBooking, booking_id)
    if not booking or booking.bot_id != registered_bot_id:
        await callback.answer("Запис не знайдено.", show_alert=True)
        return

    client = await session.get(ApptClient, booking.client_id)
    client_tid = client.telegram_id if client else None

    deposit = (await session.execute(
        select(ApptDeposit).where(ApptDeposit.booking_id == booking_id)
    )).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if action == "confirm_deposit":
        if booking.status != ApptBookingStatus.AWAITING_DEPOSIT:
            await callback.answer("⚠️ Статус запису змінився — дія недоступна.", show_alert=True)
            return
        booking.status = ApptBookingStatus.CONFIRMED
        if deposit:
            deposit.status = ApptDepositStatus.CONFIRMED
            deposit.confirmed_at = now
        await session.commit()
        await callback.answer("✅ Запис підтверджено!")

        if client_tid:
            day_ua = _DAYS_SHORT[booking.slot_date.weekday()]
            social = await get_cfg(session, registered_bot_id, _SOCIAL_TEXT, _DEFAULT_SOCIAL)
            contact = _contact_line(social)
            confirm_text = (
                f"✅ <b>Ваш запис підтверджено!</b>\n\n"
                f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n\n"
                f"Чекаємо вас!"
                + (f"\n\n{contact}" if contact else "")
            )
            try:
                await bot.send_message(chat_id=client_tid, text=confirm_text)
            except Exception as e:
                logger.warning("Could not notify client about confirmation: %s", e)

        try:
            await callback.message.edit_text(
                f"✅ Запис #{booking_id} підтверджено.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="◀️ До списку", callback_data="tttm_list:upcoming")],
                ]),
            )
        except Exception:
            pass

    elif action == "reject":
        if booking.status not in (
            ApptBookingStatus.AWAITING_DEPOSIT,
            ApptBookingStatus.PENDING,
        ):
            await callback.answer("⚠️ Статус запису змінився — дія недоступна.", show_alert=True)
            return
        booking.status = ApptBookingStatus.CANCELLED_BY_MASTER
        booking.cancel_reason = "Відхилено майстром"
        if deposit:
            deposit.status = ApptDepositStatus.RETURNED
        await session.commit()
        await callback.answer("❌ Запис відхилено.")

        if client_tid:
            try:
                await bot.send_message(
                    chat_id=client_tid,
                    text=(
                        f"😔 На жаль, майстер не може прийняти ваш запис.\n\n"
                        f"📅 {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n\n"
                        f"Депозит буде повернуто. Спробуйте обрати інший час /start"
                    ),
                )
            except Exception as e:
                logger.warning("Could not notify client about rejection: %s", e)

        try:
            await callback.message.edit_text(
                f"❌ Запис #{booking_id} відхилено.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="◀️ До списку", callback_data="tttm_list:pending")],
                ]),
            )
        except Exception:
            pass

    elif action == "complete":
        if booking.status != ApptBookingStatus.CONFIRMED:
            await callback.answer("⚠️ Статус запису змінився — дія недоступна.", show_alert=True)
            return
        if booking.slot_date > datetime.now(_TZ).date():
            await callback.answer(
                "Сеанс ще не відбувся. Цю дію можна виконати в день сеансу або пізніше.",
                show_alert=True,
            )
            return
        booking.status = ApptBookingStatus.COMPLETED
        await session.commit()
        await callback.answer("✔️ Сеанс завершено!")

        if client_tid:
            try:
                await bot.send_message(
                    chat_id=client_tid,
                    text=(
                        "🎉 <b>Дякуємо за довіру!</b>\n\n"
                        "Ваш сеанс завершено. Будемо вдячні за відгук — "
                        "це допомагає іншим клієнтам."
                    ),
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(
                            text="⭐ Залишити відгук",
                            callback_data=f"ttt_review:start:{booking_id}",
                        )],
                    ]),
                )
            except Exception as e:
                logger.warning("Could not send completion message: %s", e)

        try:
            await callback.message.edit_text(
                f"✔️ Сеанс #{booking_id} завершено.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="◀️ Записи", callback_data="tttm_list:completed")],
                ]),
            )
        except Exception:
            pass

    elif action == "noshow":
        if booking.status != ApptBookingStatus.CONFIRMED:
            await callback.answer("⚠️ Статус запису змінився — дія недоступна.", show_alert=True)
            return
        if booking.slot_date >= datetime.now(_TZ).date():
            await callback.answer(
                "No-show можна позначити тільки після дати сеансу.",
                show_alert=True,
            )
            return
        booking.status = ApptBookingStatus.NO_SHOW
        if deposit:
            deposit.status = ApptDepositStatus.KEPT
        if client:
            client.no_shows_count = (client.no_shows_count or 0) + 1
            client.rating = max(1, (client.rating or 5) - 1)
        await session.commit()
        await callback.answer("👻 No-show відмічено.")

        try:
            await callback.message.edit_text(
                f"👻 Запис #{booking_id} — клієнт не з'явився. Депозит залишено.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="◀️ Записи", callback_data="tttm_list:completed")],
                ]),
            )
        except Exception:
            pass

    elif action == "cancel_return":
        # Legacy action kept for old inline messages still in master's chat
        if booking.status not in (
            ApptBookingStatus.CONFIRMED,
            ApptBookingStatus.AWAITING_DEPOSIT,
        ):
            await callback.answer("⚠️ Статус запису змінився — дія недоступна.", show_alert=True)
            return
        booking.status = ApptBookingStatus.CANCELLED_BY_MASTER
        booking.cancel_reason = "Скасовано майстром з поверненням депозиту"
        if deposit:
            deposit.status = ApptDepositStatus.RETURNED
        await session.commit()
        await callback.answer("↩️ Скасовано, депозит повертається.")
        if client_tid:
            day_ua = _DAYS_SHORT[booking.slot_date.weekday()]
            try:
                await bot.send_message(
                    chat_id=client_tid,
                    text=(
                        f"На жаль, майстер скасував ваш запис.\n\n"
                        f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n\n"
                        "Якщо був сплачений депозит — він буде повернуто."
                    ),
                )
            except Exception as e:
                logger.warning("Could not notify client about cancellation: %s", e)
        try:
            await callback.message.edit_text(
                f"Запис #{booking_id} скасовано.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="◀️ Записи", callback_data="tttm_list:pending")],
                ]),
            )
        except Exception:
            pass

    elif action == "cancel_pre":
        mention = (
            f"@{client.username}" if client and client.username
            else (client.full_name or f"ID {client.telegram_id}") if client
            else f"#{booking_id}"
        )
        day_ua = _DAYS_SHORT[booking.slot_date.weekday()]
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"⚠️ <b>Скасувати запис?</b>\n\n"
                f"👤 {mention}\n"
                f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✅ Так, скасувати",
                            callback_data=f"tttm_bk:{booking_id}:cancel_yes",
                        ),
                        types.InlineKeyboardButton(
                            text="❌ Ні",
                            callback_data=f"tttm_bk:{booking_id}:view",
                        ),
                    ],
                ]),
            )
        except Exception:
            pass

    elif action == "cancel_yes":
        if booking.status not in (
            ApptBookingStatus.PENDING,
            ApptBookingStatus.AWAITING_DEPOSIT,
            ApptBookingStatus.CONFIRMED,
        ):
            await callback.answer("⚠️ Статус запису змінився — дія недоступна.", show_alert=True)
            return
        booking.status = ApptBookingStatus.CANCELLED_BY_MASTER
        booking.cancel_reason = "Скасовано майстром"
        if deposit:
            deposit.status = ApptDepositStatus.RETURNED
        await session.commit()
        await callback.answer("↩️ Запис скасовано.")
        if client_tid:
            day_ua = _DAYS_SHORT[booking.slot_date.weekday()]
            try:
                await bot.send_message(
                    chat_id=client_tid,
                    text=(
                        f"На жаль, майстер скасував ваш запис.\n\n"
                        f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n\n"
                        "Якщо був сплачений депозит — він буде повернуто."
                    ),
                )
            except Exception as e:
                logger.warning("Could not notify client about cancellation: %s", e)
        try:
            await callback.message.edit_text(
                f"Запис #{booking_id} скасовано.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="◀️ Записи", callback_data="tttm_list:pending")],
                ]),
            )
        except Exception:
            pass


# ── Clients ───────────────────────────────────────────────────────────────────

async def clients_list(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    clients = (await session.execute(
        select(ApptClient)
        .where(ApptClient.bot_id == registered_bot_id)
        .order_by(ApptClient.bookings_count.desc())
        .limit(20)
    )).scalars().all()

    if not clients:
        await callback.message.edit_text(
            "👥 Клієнтів поки немає.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")],
            ]),
        )
        await callback.answer()
        return

    rows = [
        [types.InlineKeyboardButton(
            text=(
                f"{'🚫 ' if c.is_blocked else ''}"
                f"{c.full_name or c.username or str(c.telegram_id)} "
                f"({c.bookings_count} зап.)"
            ),
            callback_data=f"tttm_client:{c.id}",
        )]
        for c in clients
    ]
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])
    await callback.message.edit_text(
        "👥 <b>Клієнти:</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def client_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    client_id = int(callback.data.split(":")[1])
    client = await session.get(ApptClient, client_id)
    if not client or client.bot_id != registered_bot_id:
        await callback.answer("Клієнта не знайдено.", show_alert=True)
        return

    mention = f"@{client.username}" if client.username else (client.full_name or f"ID {client.telegram_id}")
    blocked = "🚫 ЗАБЛОКОВАНИЙ\n" if client.is_blocked else ""
    notes = f"\n📝 Нотатки: {client.notes}" if client.notes else ""
    text = (
        f"{blocked}"
        f"👤 <b>{mention}</b>\n"
        f"⭐ Рейтинг: {client.rating}/5\n"
        f"📋 Записів: {client.bookings_count}\n"
        f"❌ Скасувань: {client.cancellations_count}\n"
        f"👻 No-show: {client.no_shows_count}"
        f"{notes}"
    )
    block_btn = (
        types.InlineKeyboardButton(text="✅ Розблокувати", callback_data=f"tttm_client_action:{client_id}:unblock")
        if client.is_blocked
        else
        types.InlineKeyboardButton(text="🚫 Заблокувати", callback_data=f"tttm_client_action:{client_id}:block")
    )
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="📝 Нотатка",  callback_data=f"tttm_client_action:{client_id}:note"),
                block_btn,
            ],
            [types.InlineKeyboardButton(text="◀️ Клієнти", callback_data="tttm_clients")],
        ]),
    )
    await callback.answer()


async def client_action(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    state: FSMContext,
) -> None:
    _, client_id_str, action = callback.data.split(":")
    client_id = int(client_id_str)
    client = await session.get(ApptClient, client_id)
    if not client or client.bot_id != registered_bot_id:
        await callback.answer("Помилка.", show_alert=True)
        return

    if action == "block":
        client.is_blocked = True
        await session.commit()
        await callback.answer("🚫 Клієнта заблоковано.")
        callback.data = f"tttm_client:{client_id}"
        await client_view(callback, session, registered_bot_id)

    elif action == "unblock":
        client.is_blocked = False
        await session.commit()
        await callback.answer("✅ Клієнта розблоковано.")
        callback.data = f"tttm_client:{client_id}"
        await client_view(callback, session, registered_bot_id)

    elif action == "note":
        await state.update_data(editing_client_id=client_id)
        await callback.answer()
        await callback.message.edit_text(
            "Введіть нотатку про клієнта (замінить попередню):",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Очистити нотатку", callback_data=f"tttm_client_action:{client_id}:note_clear")],
                [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data=f"tttm_client:{client_id}")],
            ]),
        )
        await state.set_state(TattooMasterFSM.client_note)


async def client_note_clear(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    state: FSMContext,
) -> None:
    _, client_id_str, _ = callback.data.split(":")
    client = await session.get(ApptClient, int(client_id_str))
    if client and client.bot_id == registered_bot_id:
        client.notes = None
        await session.commit()
    await state.clear()
    await callback.answer("Нотатку видалено.")
    callback.data = f"tttm_client:{client_id_str}"
    await client_view(callback, session, registered_bot_id)


async def client_note_text(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    client_id = data.get("editing_client_id")
    client = await session.get(ApptClient, client_id)
    if client and client.bot_id == registered_bot_id:
        client.notes = message.text.strip()
        await session.commit()
    await state.clear()
    await message.answer("✅ Нотатку збережено.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Клієнти", callback_data="tttm_clients")],
    ]))


# ── Schedule ──────────────────────────────────────────────────────────────────

def _generate_slots_m(sched: ApptSchedule) -> list[str]:
    """Generate HH:MM slot list from an ApptSchedule (same logic as client side)."""
    try:
        sh, sm = map(int, sched.start_time.split(":"))
        eh, em = map(int, sched.end_time.split(":"))
    except Exception:
        return []
    step = (sched.slot_duration_min or 60) + (sched.buffer_min or 0)
    start_min = sh * 60 + sm
    end_min   = eh * 60 + em
    slots: list[str] = []
    cur = start_min
    while cur + (sched.slot_duration_min or 60) <= end_min:
        slots.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += step
    return slots

async def schedule_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    mode = await get_cfg(session, registered_bot_id, "ttt_schedule_mode")

    if mode == "flexible":
        await callback.answer()
        await callback.message.edit_text(
            "🗓 <b>Розклад — Гнучкий режим</b>\n\n"
            "Клієнти бачать тільки ті слоти, які ви самі додасте.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="➕ Додати слот",       callback_data="tttm_flex_add")],
                [types.InlineKeyboardButton(text="📋 Мої слоти",         callback_data="tttm_flex_list")],
                [types.InlineKeyboardButton(text="◀️ Меню",             callback_data="tttm_admin:home")],
            ]),
        )
        return

    schedules = (await session.execute(
        select(ApptSchedule)
        .where(ApptSchedule.bot_id == registered_bot_id)
        .order_by(ApptSchedule.day_of_week)
    )).scalars().all()

    sched_map = {s.day_of_week: s for s in schedules}
    lines = []
    for dow in range(7):
        s = sched_map.get(dow)
        if s and s.is_active:
            lines.append(f"✅ {_DAYS_UA[dow]}: {s.start_time}–{s.end_time} (по {s.slot_duration_min} хв)")
        else:
            lines.append(f"🔴 {_DAYS_UA[dow]}: вихідний")

    text = "🗓 <b>Ваш робочий розклад:</b>\n\n" + "\n".join(lines)
    rows = [
        [types.InlineKeyboardButton(
            text=f"{'✅' if sched_map.get(i) and sched_map[i].is_active else '🔴'} {_DAYS_SHORT[i]}",
            callback_data=f"tttm_sched_day:{i}",
        )]
        for i in range(7)
    ]
    ovr_btn = [types.InlineKeyboardButton(text="📅 Слоти на конкретну дату", callback_data="tttm_sched_ovr")]
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])

    try:
        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                rows[0] + rows[1] + rows[2],
                rows[3] + rows[4],
                rows[5] + rows[6],
                ovr_btn,
                rows[7],
            ]),
        )
    except Exception:
        flat = [r[0] for r in rows[:-1]]
        combined = [flat[i:i+3] for i in range(0, len(flat), 3)]
        combined.append(ovr_btn)
        combined.append(rows[-1])
        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=combined),
        )
    await callback.answer()


async def schedule_day(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    dow = int(callback.data.split(":")[1])
    sched = (await session.execute(
        select(ApptSchedule).where(
            ApptSchedule.bot_id == registered_bot_id,
            ApptSchedule.day_of_week == dow,
        )
    )).scalar_one_or_none()

    day_name = _DAYS_UA[dow]
    if sched and sched.is_active:
        info = f"{sched.start_time}–{sched.end_time}, по {sched.slot_duration_min} хв"
    else:
        info = "вихідний"

    await callback.message.edit_text(
        f"🗓 <b>{day_name}</b>: {info}\n\nЩо змінити?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✅ Включити 10:00–20:00",
                    callback_data=f"tttm_sched_set:{dow}:10:00:20:00:60",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="✅ Включити 9:00–18:00",
                    callback_data=f"tttm_sched_set:{dow}:09:00:18:00:60",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="🔴 Встановити вихідний",
                    callback_data=f"tttm_sched_off:{dow}",
                ),
            ],
            [types.InlineKeyboardButton(text="◀️ Розклад", callback_data="tttm_schedule")],
        ]),
    )
    await callback.answer()


async def schedule_set(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    parts = callback.data.split(":")
    # tttm_sched_set:{dow}:{sh}:{sm}:{eh}:{em}:{dur}
    dow = int(parts[1])
    start = f"{parts[2]}:{parts[3]}"
    end   = f"{parts[4]}:{parts[5]}"
    dur   = int(parts[6])

    existing = (await session.execute(
        select(ApptSchedule).where(
            ApptSchedule.bot_id == registered_bot_id,
            ApptSchedule.day_of_week == dow,
        )
    )).scalar_one_or_none()

    if existing:
        existing.start_time = start
        existing.end_time   = end
        existing.slot_duration_min = dur
        existing.is_active  = True
    else:
        session.add(ApptSchedule(
            bot_id=registered_bot_id,
            day_of_week=dow,
            start_time=start,
            end_time=end,
            slot_duration_min=dur,
            is_active=True,
        ))
    await session.commit()
    await callback.answer(f"✅ {_DAYS_UA[dow]} — {start}–{end}")
    callback.data = "tttm_schedule"
    await schedule_view(callback, session, registered_bot_id)


async def schedule_off(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    dow = int(callback.data.split(":")[1])
    existing = (await session.execute(
        select(ApptSchedule).where(
            ApptSchedule.bot_id == registered_bot_id,
            ApptSchedule.day_of_week == dow,
        )
    )).scalar_one_or_none()
    if existing:
        existing.is_active = False
        await session.commit()
    await callback.answer(f"🔴 {_DAYS_UA[dow]} — вихідний")
    callback.data = "tttm_schedule"
    await schedule_view(callback, session, registered_bot_id)


# ── Schedule overrides ────────────────────────────────────────────────────────

async def _sched_ovr_get(
    session: AsyncSession, bot_id: int, d: date
) -> tuple[list[str], set[str], bool]:
    """Return (all_slots, booked_slots, has_override) for the given date."""
    override = (await session.execute(
        select(ApptScheduleOverride).where(
            ApptScheduleOverride.bot_id == bot_id,
            ApptScheduleOverride.date == d,
        )
    )).scalar_one_or_none()

    if override is not None:
        all_slots = json.loads(override.slots_json)
        has_override = True
    else:
        sched_row = (await session.execute(
            select(ApptSchedule).where(
                ApptSchedule.bot_id == bot_id,
                ApptSchedule.day_of_week == d.weekday(),
                ApptSchedule.is_active.is_(True),
            )
        )).scalar_one_or_none()
        all_slots = _generate_slots_m(sched_row) if sched_row else []
        has_override = False

    booked_rows = (await session.execute(
        select(ApptBooking.slot_time).where(
            ApptBooking.bot_id == bot_id,
            ApptBooking.slot_date == d,
            ApptBooking.status.in_([
                ApptBookingStatus.PENDING,
                ApptBookingStatus.AWAITING_DEPOSIT,
                ApptBookingStatus.CONFIRMED,
            ]),
        )
    )).scalars().all()

    return all_slots, set(booked_rows), has_override


async def _sched_ovr_save(
    session: AsyncSession, bot_id: int, d: date, slots: list[str]
) -> None:
    existing = (await session.execute(
        select(ApptScheduleOverride).where(
            ApptScheduleOverride.bot_id == bot_id,
            ApptScheduleOverride.date == d,
        )
    )).scalar_one_or_none()
    payload = json.dumps(sorted(slots))
    if existing:
        existing.slots_json = payload
    else:
        session.add(ApptScheduleOverride(bot_id=bot_id, date=d, slots_json=payload))
    await session.commit()


async def sched_ovr_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    today = datetime.now(_TZ).date()
    window_end = today + timedelta(days=14)
    override_dates = set((await session.execute(
        select(ApptScheduleOverride.date).where(
            ApptScheduleOverride.bot_id == registered_bot_id,
            ApptScheduleOverride.date > today,
            ApptScheduleOverride.date <= window_end,
        )
    )).scalars().all())

    pair: list[types.InlineKeyboardButton] = []
    rows: list[list[types.InlineKeyboardButton]] = []
    for offset in range(1, 15):
        d = today + timedelta(days=offset)
        lbl = f"{_DAYS_SHORT[d.weekday()]} {d.strftime('%d.%m')}"
        if d in override_dates:
            lbl = f"✏️ {lbl}"
        pair.append(types.InlineKeyboardButton(
            text=lbl, callback_data=f"tttm_ovr_day:{d.isoformat()}",
        ))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([types.InlineKeyboardButton(text="◀️ Розклад", callback_data="tttm_schedule")])

    await callback.answer()
    await callback.message.edit_text(
        "📅 <b>Слоти на конкретну дату</b>\n\n"
        "Оберіть дату для редагування слотів.\n"
        "<i>✏️ — є власне налаштування для дати.</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def sched_ovr_day_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    date_str = callback.data.split(":", 1)[1]
    d = date.fromisoformat(date_str)
    all_slots, booked_slots, has_override = await _sched_ovr_get(session, registered_bot_id, d)

    label = d.strftime("%d.%m.%Y")
    day_name = _DAYS_UA[d.weekday()]
    source = "⚙️ власне налаштування" if has_override else "📋 з тижневого розкладу"

    if not all_slots:
        slots_text = "<i>Немає слотів (вихідний або розклад не налаштовано).</i>"
    else:
        lines = [
            f"🔒 {s} — заброньовано" if s in booked_slots else f"✅ {s}"
            for s in all_slots
        ]
        slots_text = "\n".join(lines)

    rows: list[list[types.InlineKeyboardButton]] = []
    for s in all_slots:
        if s not in booked_slots:
            rows.append([types.InlineKeyboardButton(
                text=f"🗑 Видалити {s}",
                callback_data=f"tttm_ovr_del:{date_str}:{s}",
            )])
    rows.append([types.InlineKeyboardButton(
        text="➕ Додати слот", callback_data=f"tttm_ovr_add:{date_str}",
    )])
    if has_override:
        rows.append([types.InlineKeyboardButton(
            text="🔄 Скинути до розкладу", callback_data=f"tttm_ovr_reset:{date_str}",
        )])
    rows.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="tttm_sched_ovr")])

    await callback.answer()
    await callback.message.edit_text(
        f"📅 <b>{day_name}, {label}</b>\n"
        f"<i>Джерело: {source}</i>\n\n"
        f"{slots_text}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def sched_ovr_del(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    parts = callback.data.split(":", 2)
    date_str, slot = parts[1], parts[2]
    d = date.fromisoformat(date_str)
    all_slots, booked_slots, _ = await _sched_ovr_get(session, registered_bot_id, d)

    if slot in booked_slots:
        await callback.answer("⛔ Цей слот вже заброньований.", show_alert=True)
        return

    new_slots = [s for s in all_slots if s != slot]
    await _sched_ovr_save(session, registered_bot_id, d, new_slots)
    callback.data = f"tttm_ovr_day:{date_str}"
    await sched_ovr_day_view(callback, session, registered_bot_id)


async def sched_ovr_add(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    date_str = callback.data.split(":", 1)[1]
    await state.update_data(sched_ovr_date=date_str)
    await state.set_state(TattooMasterFSM.sched_ovr_slot_input)
    await callback.answer()
    await callback.message.edit_text(
        "⏰ Введіть час слоту у форматі <b>HH:MM</b> (наприклад: 15:30):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text="❌ Скасувати", callback_data=f"tttm_ovr_day:{date_str}",
            ),
        ]]),
    )


async def sched_ovr_slot_text(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    text = (message.text or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        await message.answer("⚠️ Невірний формат. Введіть час у форматі HH:MM (наприклад: 15:30):")
        return
    h, m = map(int, text.split(":"))
    if h > 23 or m > 59:
        await message.answer("⚠️ Час поза межами 00:00–23:59. Спробуйте ще раз:")
        return

    slot = f"{h:02d}:{m:02d}"
    data = await state.get_data()
    date_str = data.get("sched_ovr_date")
    if not date_str:
        await state.clear()
        await message.answer("⚠️ Сесія закінчилась. Почніть знову через розклад.")
        return

    d = date.fromisoformat(date_str)
    all_slots, _, _ = await _sched_ovr_get(session, registered_bot_id, d)

    if slot in all_slots:
        await message.answer(f"⚠️ Слот {slot} вже є для цієї дати. Введіть інший час:")
        return

    await _sched_ovr_save(session, registered_bot_id, d, all_slots + [slot])
    await state.clear()

    label = d.strftime("%d.%m.%Y")
    await message.answer(
        f"✅ Слот <b>{slot}</b> додано на {label}.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(
                text=f"📅 {label}", callback_data=f"tttm_ovr_day:{date_str}",
            ),
            types.InlineKeyboardButton(text="◀️ Розклад", callback_data="tttm_schedule"),
        ]]),
    )


async def sched_ovr_reset(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    date_str = callback.data.split(":", 1)[1]
    d = date.fromisoformat(date_str)
    override = (await session.execute(
        select(ApptScheduleOverride).where(
            ApptScheduleOverride.bot_id == registered_bot_id,
            ApptScheduleOverride.date == d,
        )
    )).scalar_one_or_none()
    if override:
        await session.delete(override)
        await session.commit()
    callback.data = f"tttm_ovr_day:{date_str}"
    await sched_ovr_day_view(callback, session, registered_bot_id)


# ── Flexible schedule (manual slot management) ────────────────────────────────

async def sched_flex_add_date(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    """Date picker — choose date to add a slot on."""
    today = datetime.now(_TZ).date()
    pair: list[types.InlineKeyboardButton] = []
    rows: list[list[types.InlineKeyboardButton]] = []
    for offset in range(1, 29):  # 4 weeks
        d = today + timedelta(days=offset)
        pair.append(types.InlineKeyboardButton(
            text=f"{_DAYS_SHORT[d.weekday()]} {d.strftime('%d.%m')}",
            callback_data=f"tttm_flex_add_day:{d.isoformat()}",
        ))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="tttm_schedule")])

    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Додати слот — оберіть дату:</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def sched_flex_add_day(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    """Ask for HH:MM time after date is selected."""
    date_str = callback.data.split(":", 1)[1]
    await state.update_data(sched_flex_date=date_str)
    await state.set_state(TattooMasterFSM.sched_flex_time_input)
    await callback.answer()
    await callback.message.edit_text(
        f"➕ Введіть час слоту у форматі <b>HH:MM</b> (наприклад: 15:30):\n"
        f"<i>Дата: {date_str}</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="❌ Скасувати", callback_data="tttm_flex_add"),
        ]]),
    )


async def sched_flex_slot_text(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    text = (message.text or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        await message.answer("⚠️ Невірний формат. Введіть час у форматі HH:MM (наприклад: 15:30):")
        return
    h, m = map(int, text.split(":"))
    if h > 23 or m > 59:
        await message.answer("⚠️ Час поза межами 00:00–23:59. Спробуйте ще раз:")
        return

    slot = f"{h:02d}:{m:02d}"
    data = await state.get_data()
    date_str = data.get("sched_flex_date")
    if not date_str:
        await state.clear()
        await message.answer("⚠️ Сесія закінчилась. Почніть знову через розклад.")
        return

    d = date.fromisoformat(date_str)
    all_slots, _, _ = await _sched_ovr_get(session, registered_bot_id, d)

    if slot in all_slots:
        await message.answer(f"⚠️ Слот {slot} вже є для цієї дати. Введіть інший час:")
        return

    await _sched_ovr_save(session, registered_bot_id, d, all_slots + [slot])
    await state.clear()

    label = d.strftime("%d.%m.%Y")
    await message.answer(
        f"✅ Слот <b>{slot}</b> додано на {label}.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="➕ Додати ще", callback_data="tttm_flex_add"),
            types.InlineKeyboardButton(text="📋 Мої слоти", callback_data="tttm_flex_list"),
        ]]),
    )


async def sched_flex_list(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    """List all future manually-added slots grouped by date."""
    today = datetime.now(_TZ).date()
    overrides = (await session.execute(
        select(ApptScheduleOverride).where(
            ApptScheduleOverride.bot_id == registered_bot_id,
            ApptScheduleOverride.date > today,
        ).order_by(ApptScheduleOverride.date)
    )).scalars().all()

    if not overrides:
        await callback.answer()
        await callback.message.edit_text(
            "📋 <b>Мої слоти</b>\n\nЖодного слоту ще не додано.\nДодайте перший слот через «➕ Додати слот».",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="➕ Додати слот", callback_data="tttm_flex_add")],
                [types.InlineKeyboardButton(text="◀️ Розклад",    callback_data="tttm_schedule")],
            ]),
        )
        return

    # Load bookings to mark booked slots
    booked_rows = (await session.execute(
        select(ApptBooking.slot_date, ApptBooking.slot_time).where(
            ApptBooking.bot_id == registered_bot_id,
            ApptBooking.status.in_([
                ApptBookingStatus.PENDING,
                ApptBookingStatus.AWAITING_DEPOSIT,
                ApptBookingStatus.CONFIRMED,
            ]),
        )
    )).all()
    booked = {(r.slot_date, r.slot_time) for r in booked_rows}

    lines = []
    rows: list[list[types.InlineKeyboardButton]] = []
    for o in overrides:
        d = o.date
        day_lbl = f"📅 {_DAYS_UA[d.weekday()]}, {d.strftime('%d.%m.%Y')}"
        slots = sorted(json.loads(o.slots_json))
        slot_parts = []
        for s in slots:
            if (d, s) in booked:
                slot_parts.append(f"🔒 {s}")
                rows.append([types.InlineKeyboardButton(
                    text=f"🔒 {d.strftime('%d.%m')} {s} (заброньовано)",
                    callback_data="pa:noop",
                )])
            else:
                slot_parts.append(f"✅ {s}")
                rows.append([types.InlineKeyboardButton(
                    text=f"🗑 {d.strftime('%d.%m')} {s}",
                    callback_data=f"tttm_flex_del:{d.isoformat()}:{s}",
                )])
        lines.append(f"{day_lbl}\n" + "  ".join(slot_parts))

    rows.append([
        types.InlineKeyboardButton(text="➕ Додати слот", callback_data="tttm_flex_add"),
        types.InlineKeyboardButton(text="◀️ Розклад",    callback_data="tttm_schedule"),
    ])

    text = "📋 <b>Мої слоти:</b>\n\n" + "\n\n".join(lines)
    await callback.answer()
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def sched_flex_del(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    """Show delete confirmation for a flex slot."""
    parts = callback.data.split(":", 2)
    date_str, slot = parts[1], parts[2]
    d = date.fromisoformat(date_str)
    label = f"{_DAYS_UA[d.weekday()]}, {d.strftime('%d.%m.%Y')} о {slot}"

    await callback.answer()
    await callback.message.edit_text(
        f"❓ <b>Видалити слот?</b>\n\n📅 {label}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✅ Так, видалити",
                    callback_data=f"tttm_flex_del_yes:{date_str}:{slot}",
                ),
                types.InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data="tttm_flex_list",
                ),
            ],
        ]),
    )


async def sched_flex_del_yes(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    """Delete a specific slot from the override for that date."""
    parts = callback.data.split(":", 2)
    date_str, slot = parts[1], parts[2]
    d = date.fromisoformat(date_str)

    all_slots, booked_slots, _ = await _sched_ovr_get(session, registered_bot_id, d)

    if slot in booked_slots:
        await callback.answer("⛔ Цей слот вже заброньований.", show_alert=True)
        return

    new_slots = [s for s in all_slots if s != slot]
    if new_slots:
        await _sched_ovr_save(session, registered_bot_id, d, new_slots)
    else:
        # No slots left — delete the override row entirely
        override = (await session.execute(
            select(ApptScheduleOverride).where(
                ApptScheduleOverride.bot_id == registered_bot_id,
                ApptScheduleOverride.date == d,
            )
        )).scalar_one_or_none()
        if override:
            await session.delete(override)
            await session.commit()

    await callback.answer("🗑 Видалено.")
    callback.data = "tttm_flex_list"
    await sched_flex_list(callback, session, registered_bot_id)


# ── Blocked dates ─────────────────────────────────────────────────────────────

async def blocked_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    blocked = (await session.execute(
        select(ApptBlockedDate)
        .where(ApptBlockedDate.bot_id == registered_bot_id)
        .order_by(ApptBlockedDate.date_start)
    )).scalars().all()

    today = datetime.now(_TZ).date()
    active = [b for b in blocked if b.date_end >= today]

    if not active:
        text = "🚫 <b>Заблоковані дати:</b>\n\nПоки немає. Додайте відпустку або вихідний."
    else:
        lines = [
            f"• {b.date_start.strftime('%d.%m')}–{b.date_end.strftime('%d.%m.%Y')}"
            + (f" ({b.reason})" if b.reason else "")
            for b in active
        ]
        text = "🚫 <b>Заблоковані дати:</b>\n\n" + "\n".join(lines)

    del_rows = [
        [types.InlineKeyboardButton(
            text=f"🗑 {b.date_start.strftime('%d.%m')}–{b.date_end.strftime('%d.%m')}",
            callback_data=f"tttm_block_del:{b.id}",
        )]
        for b in active
    ]
    del_rows.append([
        types.InlineKeyboardButton(text="➕ Додати дату/відпустку", callback_data="tttm_block_add"),
    ])
    del_rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=del_rows),
    )
    await callback.answer()


async def blocked_add_start(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.message.edit_text(
        "Введіть <b>дату початку</b> у форматі ДД.ММ.РРРР\n(наприклад: 01.07.2026):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="tttm_blocked")],
        ]),
    )
    await state.set_state(TattooMasterFSM.block_date_start)
    await callback.answer()


async def blocked_date_start(
    message: types.Message,
    state: FSMContext,
) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Невірний формат. Введіть ДД.ММ.РРРР (наприклад: 01.07.2026):")
        return
    await state.update_data(block_start=d.isoformat())
    await message.answer(
        "Введіть <b>дату кінця</b> (включно) у форматі ДД.ММ.РРРР\n"
        "Для одного дня введіть ту саму дату:"
    )
    await state.set_state(TattooMasterFSM.block_date_end)


async def blocked_date_end(
    message: types.Message,
    state: FSMContext,
) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Невірний формат. Введіть ДД.ММ.РРРР:")
        return
    await state.update_data(block_end=d.isoformat())
    await message.answer(
        "Вкажіть причину (або надішліть «-» щоб пропустити):"
    )
    await state.set_state(TattooMasterFSM.block_reason)


async def blocked_reason(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    reason = message.text.strip()
    if reason == "-":
        reason = None

    session.add(ApptBlockedDate(
        bot_id=registered_bot_id,
        date_start=date.fromisoformat(data["block_start"]),
        date_end=date.fromisoformat(data["block_end"]),
        reason=reason,
    ))
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Дати {data['block_start']} – {data['block_end']} заблоковано.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🚫 До заблокованих", callback_data="tttm_blocked")],
            [types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")],
        ]),
    )


async def blocked_delete(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    b_id = int(callback.data.split(":")[1])
    b = await session.get(ApptBlockedDate, b_id)
    if not b or b.bot_id != registered_bot_id:
        await callback.answer("Не знайдено.")
        return
    date_line = f"{b.date_start.strftime('%d.%m.%Y')} – {b.date_end.strftime('%d.%m.%Y')}"
    reason_line = f"\n📝 {b.reason}" if b.reason else ""
    await callback.message.edit_text(
        f"❓ <b>Видалити заблоковані дати?</b>\n\n{date_line}{reason_line}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Так, видалити", callback_data=f"tttm_block_del_yes:{b_id}"),
                types.InlineKeyboardButton(text="❌ Скасувати",     callback_data="tttm_blocked"),
            ],
        ]),
    )
    await callback.answer()


async def blocked_delete_confirm(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    b_id = int(callback.data.split(":")[1])
    b = await session.get(ApptBlockedDate, b_id)
    if b and b.bot_id == registered_bot_id:
        await session.delete(b)
        await session.commit()
    await callback.answer("🗑 Видалено.")
    callback.data = "tttm_blocked"
    await blocked_view(callback, session, registered_bot_id)


# ── Settings ──────────────────────────────────────────────────────────────────

async def settings_view(callback: types.CallbackQuery) -> None:
    from app.bot.handlers.niche.tattoo.settings import show_settings_menu
    await show_settings_menu(callback)


# ── Portfolio (admin side) ─────────────────────────────────────────────────────

async def admin_portfolio(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    result = await session.execute(
        select(TattooPortfolio)
        .where(TattooPortfolio.bot_id == registered_bot_id)
        .order_by(TattooPortfolio.created_at.desc())
        .limit(10)
    )
    works = list(result.scalars().all())
    count = len(works)
    rows = [
        [types.InlineKeyboardButton(
            text=f"🎨 {w.style} | {w.price} | 👁 {w.view_count}",
            callback_data=f"tttm_portfolio_view:{w.id}",
        )]
        for w in works
    ]
    rows.append([types.InlineKeyboardButton(text="➕ Додати роботу", callback_data="tttm_portfolio_add")])
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])
    text = (
        f"🎨 <b>Портфоліо</b> ({count} робіт):\n\n"
        "Натисніть на роботу щоб <b>переглянути</b>:"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


async def portfolio_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    bot: Bot,
) -> None:
    work_id = int(callback.data.split(":")[1])
    work = await session.get(TattooPortfolio, work_id)
    if not work or work.bot_id != registered_bot_id:
        await callback.answer("Не знайдено.", show_alert=True)
        return
    await callback.answer()
    caption = (
        f"🎨 <b>{work.style}</b>\n\n"
        f"{work.description}\n\n"
        f"⏱ Час виконання: {work.work_time}\n"
        f"💰 Ціна: {work.price}\n"
        f"👁 Переглядів клієнтами: {work.view_count}"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🗑 Видалити цю роботу", callback_data=f"tttm_portfolio_del:{work_id}")],
        [types.InlineKeyboardButton(text="◀️ До портфоліо", callback_data="tttm_portfolio")],
    ])
    try:
        await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=work.photo_id,
            caption=caption,
            reply_markup=kb,
        )
    except Exception:
        await callback.message.answer(caption, reply_markup=kb)


async def portfolio_add_start(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.message.edit_text(
        "Надішліть <b>фото</b> роботи:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="tttm_portfolio")],
        ]),
    )
    await state.set_state(TattooMasterFSM.portfolio_photo)
    await callback.answer()


async def portfolio_add_photo(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    if not message.photo:
        await message.answer("Надішліть фото:")
        return
    await state.update_data(portfolio_photo_id=message.photo[-1].file_id)
    await state.set_state(TattooMasterFSM.portfolio_style)

    styles: list = await get_json(session, registered_bot_id, TTT_STYLES, [])
    if styles:
        rows = [
            [types.InlineKeyboardButton(text=s, callback_data=f"tttm_pf_style:{s}")]
            for s in styles
        ]
        rows.append([types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="tttm_portfolio")])
        await message.answer(
            "Оберіть <b>стиль</b> цієї роботи:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        )
    else:
        await message.answer(
            "Введіть <b>стиль</b> (наприклад: Реалізм, Blackwork):\n\n"
            "<i>💡 Додайте стилі в ⚙️ Налаштування → 🖼 Стилі — і наступного разу вони з'являться кнопками.</i>",
        )


async def portfolio_pick_style(callback: types.CallbackQuery, state: FSMContext) -> None:
    style = callback.data[len("tttm_pf_style:"):]
    await state.update_data(portfolio_style=style)
    await state.set_state(TattooMasterFSM.portfolio_desc)
    await callback.answer()
    await callback.message.edit_text(
        f"✅ Стиль: <b>{style}</b>\n\nОпис роботи (коротко, 1–2 речення):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="tttm_portfolio")],
        ]),
    )


async def portfolio_add_style(message: types.Message, state: FSMContext) -> None:
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("Введіть назву стилю:")
        return
    await state.update_data(portfolio_style=message.text.strip())
    await message.answer("Опис роботи (коротко):")
    await state.set_state(TattooMasterFSM.portfolio_desc)


async def portfolio_add_desc(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Введіть опис:")
        return
    await state.update_data(portfolio_desc=message.text.strip())
    await message.answer("Час виконання (наприклад: 4 год):")
    await state.set_state(TattooMasterFSM.portfolio_time)


async def portfolio_add_time(message: types.Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Введіть час:")
        return
    await state.update_data(portfolio_time=message.text.strip())
    await message.answer("Ціна (наприклад: від 3 000 грн):")
    await state.set_state(TattooMasterFSM.portfolio_price)


async def portfolio_add_price(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    if not message.text:
        await message.answer("Введіть ціну:")
        return
    data = await state.get_data()
    session.add(TattooPortfolio(
        bot_id=registered_bot_id,
        style=data["portfolio_style"],
        photo_id=data["portfolio_photo_id"],
        description=data["portfolio_desc"],
        work_time=data["portfolio_time"],
        price=message.text.strip(),
    ))
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Роботу додано до портфоліо!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🎨 Портфоліо", callback_data="tttm_portfolio")],
            [types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")],
        ]),
    )


async def portfolio_delete(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    work_id = int(callback.data.split(":")[1])
    work = await session.get(TattooPortfolio, work_id)
    if work and work.bot_id == registered_bot_id:
        await session.delete(work)
        await session.commit()
        await callback.answer("🗑 Видалено.")
    else:
        await callback.answer("Не знайдено.", show_alert=True)
        return
    callback.data = "tttm_portfolio"
    await admin_portfolio(callback, session, registered_bot_id)


# ── Help ─────────────────────────────────────────────────────────────────────

def _help_back_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ До довідки", callback_data="tttm_help:menu")],
    ])


_HELP_SECTIONS = {
    "overview": (
        "🚀 <b>Як все працює</b>\n\n"
        "Клієнт відкриває ваш бот і бачить головне меню: портфоліо, прайс, відгуки та кнопку для запису. "
        "Натиснувши «Записатись», бот послідовно уточнює стиль тату, місце та розмір, пропонує завантажити референс "
        "і ставить питання з вашої анкети (наприклад, про алергію).\n\n"
        "Після цього клієнт обирає вільну дату в календарі та слот з часом. Далі — залежно від ваших налаштувань:\n"
        "• <b>Якщо депозит увімкнено</b> — бот просить клієнта надіслати скріншот оплати, а ви вручну "
        "підтверджуєте запис після перевірки банку.\n"
        "• <b>Якщо депозит вимкнено</b> — запис підтверджується одразу автоматично.\n\n"
        "Після сеансу ви позначаєте його завершеним у розділі «Записи» — клієнт отримує прохання залишити відгук.\n\n"
        "Усі записи, клієнти та виплати зберігаються в розділах <b>Записи</b> та <b>Клієнти</b> у вашій панелі."
    ),
    "deposit": (
        "💳 <b>Депозит і підтвердження</b>\n\n"
        "<b>Якщо депозит увімкнено:</b>\n"
        "Клієнт після вибору часу отримує інструкцію: суму, номер картки та призначення платежу. "
        "Він переводить гроші та надсилає скріншот прямо в бот.\n\n"
        "⚠️ <b>Важливо:</b> скріншот — це лише фото від клієнта. Бот не перевіряє оплату автоматично і нікуди "
        "не підключений до банку. Ви отримуєте сповіщення і маєте діяти вручну:\n"
        "1. Зайдіть у свій банк і переконайтесь, що кошти справді надійшли.\n"
        "2. Відкрийте запис у боті: <b>Записи → Очікують</b> → оберіть запис → натисніть <b>«✅ Підтвердити депозит»</b>.\n\n"
        "Тільки після цього клієнт отримає підтвердження. До цього моменту він бачить статус «очікує підтвердження».\n\n"
        "Якщо оплата не підтвердилась — натисніть <b>«❌ Відхилити»</b>, і запис буде скасовано.\n\n"
        "<b>Якщо депозит вимкнено:</b>\n"
        "Запис підтверджується одразу автоматично — без скріншотів і без участі майстра."
    ),
    "schedule": (
        "📅 <b>Редагування розкладу</b>\n\n"
        "<b>Спосіб 1 — швидке редагування</b>\n"
        "Панель майстра → 🗓 Розклад\n"
        "Натисніть на потрібний день тижня і виберіть:\n"
        "• «✅ Включити 10:00–20:00» або «✅ Включити 9:00–18:00» — стандартні години із слотами по 60 хв\n"
        "• «🔴 Встановити вихідний» — вимкнути цей день\n\n"
        "<b>Спосіб 2 — повне редагування</b>\n"
        "⚙️ Налаштування → 🗓 Розклад та вихідні → Змінити розклад\n"
        "Тут можна обрати довільні дні, задати власний час початку/кінця, тривалість сеансу та паузу між ними.\n\n"
        "<b>Відпустка або вихідний на конкретну дату:</b>\n"
        "Панель майстра → 🚫 Відпустка → Додати дату.\n"
        "Введіть дату початку і кінця (для одного дня — обидві однакові). Клієнти не зможуть записатися на ці дати.\n"
        "Щоб скасувати — зайдіть туди ж і видаліть блокування."
    ),
    "settings": (
        "⚙️ <b>Розділи налаштувань</b>\n\n"
        "<b>👤 Профіль майстра</b> — ваше ім'я, опис і місто. Клієнт бачить це у профілі бота.\n\n"
        "<b>🗓 Розклад та вихідні</b> — повне редагування робочих днів, годин і тривалості сеансів.\n\n"
        "<b>🎨 Послуги та ціни</b> — перелік послуг, що відображаються в прайсі.\n\n"
        "<b>🖼 Стилі</b> — стилі тату, які ви пропонуєте. Клієнт обирає стиль при записі.\n\n"
        "<b>💳 Депозит</b> — увімкнути/вимкнути депозит, встановити суму, картку та призначення платежу.\n\n"
        "<b>📋 Анкета клієнта</b> — які питання бот ставить при записі. Вимкніть ті, що вам не потрібні.\n\n"
        "<b>🔔 Нагадування</b> — налаштування того, коли надсилати нагадування клієнтам про запис.\n\n"
        "<b>💬 Шаблони повідомлень</b> — тексти, що бот надсилає клієнту: привітання, підтвердження, "
        "нагадування, догляд після сеансу, прохання про відгук. Можна редагувати під ваш стиль.\n\n"
        "<b>🚫 Обмеження</b> — мінімальний вік клієнта та ліміт годин для безкоштовного скасування."
    ),
    "reset": (
        "🔄 <b>Скидання налаштувань бота</b>\n\n"
        "<b>Що видаляється при скиданні:</b>\n"
        "• Усі налаштування: ім'я, опис, місто, стилі, розклад, депозит, шаблони повідомлень\n"
        "• Список послуг і прайс\n\n"
        "<b>Що НЕ видаляється:</b>\n"
        "• Реальна історія записів клієнтів\n"
        "• Список клієнтів та їхні дані\n"
        "• Записи про оплату депозитів\n\n"
        "Після скидання бот запустить майстер налаштувань заново — ви пройдете всі кроки ще раз.\n\n"
        "⚠️ Поки налаштування не завершено, клієнти бачать повідомлення «Майстер ще налаштовує бота» "
        "і не можуть записатись."
    ),
    "faq": (
        "❓ <b>Часті питання</b>\n\n"
        "<b>Клієнт написав, що оплатив — але запис не підтверджено. Чому?</b>\n"
        "Підтвердження — це ваша дія, не автоматика. Зайдіть у <b>Записи → Очікують</b>, відкрийте запис, "
        "перевірте банк і натисніть «✅ Підтвердити депозит».\n\n"
        "<b>Чи може клієнт скасувати запис самостійно?</b>\n"
        "Тільки до того, як він надіслав скріншот оплати. Після відправки скріншоту кнопка скасування зникає. "
        "Якщо клієнт хоче скасувати пізніше — він має написати вам, а ви скасовуєте через "
        "<b>Записи → відкрити запис → «❌ Скасувати (повернути депозит)»</b>.\n\n"
        "<b>Хочу взяти вихідний на один день. Як?</b>\n"
        "Панель майстра → 🚫 Відпустка → Додати дату. Введіть ту саму дату в обох полях.\n\n"
        "<b>Клієнт не з'явився на сеанс. Що робити?</b>\n"
        "Знайдіть запис у <b>Записи → Майбутні</b>, відкрийте його і натисніть «👻 No-show». "
        "Якщо був депозит — він залишається у вас.\n\n"
        "<b>Чому нагадування не надходять клієнтам?</b>\n"
        "Нагадування надсилаються автоматично за налаштованими інтервалами після підтвердження запису. "
        "Якщо нагадування не надходять — перевірте що інтервали увімкнені в <b>Налаштування → Нагадування</b>."
    ),
}

_HELP_MENU_KB = types.InlineKeyboardMarkup(inline_keyboard=[
    [types.InlineKeyboardButton(text="🚀 Як все працює",          callback_data="tttm_help:overview")],
    [types.InlineKeyboardButton(text="💳 Депозит і підтвердження", callback_data="tttm_help:deposit")],
    [types.InlineKeyboardButton(text="📅 Редагування розкладу",    callback_data="tttm_help:schedule")],
    [types.InlineKeyboardButton(text="⚙️ Розділи налаштувань",     callback_data="tttm_help:settings")],
    [types.InlineKeyboardButton(text="🔄 Скидання бота",           callback_data="tttm_help:reset")],
    [types.InlineKeyboardButton(text="❓ Часті питання",            callback_data="tttm_help:faq")],
    [types.InlineKeyboardButton(text="◀️ Меню",                    callback_data="tttm_admin:home")],
])


async def help_handler(callback: types.CallbackQuery) -> None:
    section = callback.data.split(":", 1)[1]
    await callback.answer()
    if section == "menu":
        try:
            await callback.message.edit_text(
                "📖 <b>Довідка</b>\n\nОберіть тему:",
                reply_markup=_HELP_MENU_KB,
            )
        except Exception:
            await callback.message.answer(
                "📖 <b>Довідка</b>\n\nОберіть тему:",
                reply_markup=_HELP_MENU_KB,
            )
        return

    text = _HELP_SECTIONS.get(section)
    if text is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=_help_back_kb())
    except Exception:
        await callback.message.answer(text, reply_markup=_help_back_kb())


# ── Admin home ────────────────────────────────────────────────────────────────

async def admin_home(
    callback: types.CallbackQuery,
) -> None:
    await callback.message.edit_text(
        "⚙️ <b>Панель майстра</b>\n\nОберіть розділ:",
        reply_markup=_admin_markup(),
    )
    await callback.answer()


async def master_catchall_text(
    message: types.Message,
    owner_telegram_id: int,
) -> None:
    """Silently delete random text from the master when no FSM state is active."""
    if message.from_user and message.from_user.id == owner_telegram_id:
        try:
            await message.delete()
        except Exception:
            pass


# ── Handler registration ───────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    # Admin home
    dp.callback_query.register(admin_home, F.data == "tttm_admin:home")

    # Help
    dp.callback_query.register(help_handler, F.data.startswith("tttm_help:"))

    # Booking list + single booking
    dp.callback_query.register(admin_records_home, F.data == "tttm_records")
    dp.callback_query.register(admin_list,    F.data.startswith("tttm_list:"))
    dp.callback_query.register(booking_view,  F.data.func(lambda d: d.startswith("tttm_bk:") and d.endswith(":view")))
    dp.callback_query.register(booking_action, F.data.func(
        lambda d: d.startswith("tttm_bk:") and not d.endswith(":view")
    ))

    # Clients
    dp.callback_query.register(clients_list,    F.data == "tttm_clients")
    dp.callback_query.register(client_view,     F.data.startswith("tttm_client:") & ~F.data.startswith("tttm_client_action:"))
    dp.callback_query.register(client_action,   F.data.startswith("tttm_client_action:") & ~F.data.endswith(":note_clear"))
    dp.callback_query.register(client_note_clear, F.data.func(lambda d: d.startswith("tttm_client_action:") and d.endswith(":note_clear")))
    dp.message.register(client_note_text, TattooMasterFSM.client_note, F.text)

    # Schedule
    dp.callback_query.register(schedule_view, F.data == "tttm_schedule")
    dp.callback_query.register(schedule_day,  F.data.startswith("tttm_sched_day:"))
    dp.callback_query.register(schedule_set,  F.data.startswith("tttm_sched_set:"))
    dp.callback_query.register(schedule_off,  F.data.startswith("tttm_sched_off:"))

    # Schedule overrides (per-date slot management — fixed mode)
    dp.callback_query.register(sched_ovr_view,     F.data == "tttm_sched_ovr")
    dp.callback_query.register(sched_ovr_day_view, F.data.startswith("tttm_ovr_day:"))
    dp.callback_query.register(sched_ovr_del,      F.data.startswith("tttm_ovr_del:"))
    dp.callback_query.register(sched_ovr_add,      F.data.startswith("tttm_ovr_add:"))
    dp.callback_query.register(sched_ovr_reset,    F.data.startswith("tttm_ovr_reset:"))
    dp.message.register(sched_ovr_slot_text, TattooMasterFSM.sched_ovr_slot_input, F.text)

    # Flexible schedule (manual slot management)
    dp.callback_query.register(sched_flex_add_date, F.data == "tttm_flex_add")
    dp.callback_query.register(sched_flex_add_day,  F.data.startswith("tttm_flex_add_day:"))
    dp.callback_query.register(sched_flex_list,     F.data == "tttm_flex_list")
    dp.callback_query.register(sched_flex_del,      F.data.startswith("tttm_flex_del:") & ~F.data.startswith("tttm_flex_del_yes:"))
    dp.callback_query.register(sched_flex_del_yes,  F.data.startswith("tttm_flex_del_yes:"))
    dp.message.register(sched_flex_slot_text, TattooMasterFSM.sched_flex_time_input, F.text)

    # Blocked dates
    dp.callback_query.register(blocked_view,       F.data == "tttm_blocked")
    dp.callback_query.register(blocked_add_start,  F.data == "tttm_block_add")
    dp.callback_query.register(blocked_delete_confirm, F.data.startswith("tttm_block_del_yes:"))
    dp.callback_query.register(blocked_delete,         F.data.startswith("tttm_block_del:"))
    dp.message.register(blocked_date_start, TattooMasterFSM.block_date_start, F.text)
    dp.message.register(blocked_date_end,   TattooMasterFSM.block_date_end,   F.text)
    dp.message.register(blocked_reason,     TattooMasterFSM.block_reason,     F.text)

    # Settings
    dp.callback_query.register(settings_view, F.data == "tttm_settings")

    # Portfolio admin
    dp.callback_query.register(admin_portfolio,    F.data == "tttm_portfolio")
    dp.callback_query.register(portfolio_add_start, F.data == "tttm_portfolio_add")
    dp.callback_query.register(portfolio_view,     F.data.startswith("tttm_portfolio_view:"))
    dp.callback_query.register(portfolio_delete,   F.data.startswith("tttm_portfolio_del:"))
    dp.callback_query.register(portfolio_pick_style, TattooMasterFSM.portfolio_style, F.data.startswith("tttm_pf_style:"))
    dp.message.register(portfolio_add_photo, TattooMasterFSM.portfolio_photo, F.photo)
    dp.message.register(portfolio_add_style, TattooMasterFSM.portfolio_style, F.text)
    dp.message.register(portfolio_add_desc,  TattooMasterFSM.portfolio_desc,  F.text)
    dp.message.register(portfolio_add_time,  TattooMasterFSM.portfolio_time,  F.text)
    dp.message.register(portfolio_add_price, TattooMasterFSM.portfolio_price, F.text)

    # Catch-all: delete random text from master when no FSM active (StateFilter(None) = no active state)
    dp.message.register(master_catchall_text, StateFilter(None), F.text)
