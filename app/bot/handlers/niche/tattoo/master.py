"""Master/admin handlers for the TATTOO niche — booking management, schedule, clients."""
import logging
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import (
    ApptBlockedDate, ApptBooking, ApptBookingStatus,
    ApptClient, ApptDeposit, ApptDepositStatus, ApptSchedule,
)
from app.models.tattoo import TattooPortfolio, TattooReview, ReviewStatus, TattooService

logger = logging.getLogger(__name__)

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
    deposit_card     = State()
    deposit_amount   = State()
    welcome_text     = State()


# ── Admin menu ────────────────────────────────────────────────────────────────

def _admin_markup() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📋 Записи",    callback_data="tttm_list:pending"),
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


async def admin_list(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    tab = callback.data.split(":")[1]
    statuses = _STATUS_FILTERS.get(tab, _STATUS_FILTERS["pending"])

    rows = (await session.execute(
        select(ApptBooking)
        .where(
            ApptBooking.bot_id == registered_bot_id,
            ApptBooking.status.in_(statuses),
        )
        .order_by(ApptBooking.slot_date, ApptBooking.slot_time)
        .limit(20)
    )).scalars().all()

    tabs_row = [
        types.InlineKeyboardButton(text="⏳ Нові",       callback_data="tttm_list:pending"),
        types.InlineKeyboardButton(text="✅ Майбутні",   callback_data="tttm_list:upcoming"),
        types.InlineKeyboardButton(text="📁 Завершені",  callback_data="tttm_list:completed"),
    ]

    if not rows:
        await callback.message.edit_text(
            f"Записів у розділі немає.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                tabs_row,
                [types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")],
            ]),
        )
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
    bk_rows.append(tabs_row)
    bk_rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])

    try:
        await callback.message.edit_text(
            f"📋 <b>Записи ({tab}):</b>",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=bk_rows),
        )
    except Exception:
        await callback.message.answer(
            f"📋 <b>Записи ({tab}):</b>",
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
                callback_data=f"tttm_bk:{booking_id}:reject",
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
                text="❌ Скасувати (повернути депозит)",
                callback_data=f"tttm_bk:{booking_id}:cancel_return",
            ),
        ])
    if booking.status == ApptBookingStatus.PENDING:
        kb_rows.append([
            types.InlineKeyboardButton(
                text="❌ Відхилити",
                callback_data=f"tttm_bk:{booking_id}:reject",
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
        booking.status = ApptBookingStatus.CONFIRMED
        if deposit:
            deposit.status = ApptDepositStatus.CONFIRMED
            deposit.confirmed_at = now
        await session.commit()
        await callback.answer("✅ Запис підтверджено!")

        if client_tid:
            day_ua = _DAYS_SHORT[booking.slot_date.weekday()]
            try:
                await bot.send_message(
                    chat_id=client_tid,
                    text=(
                        f"✅ <b>Ваш запис підтверджено!</b>\n\n"
                        f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n\n"
                        f"Чекаємо вас! Якщо щось зміниться — напишіть заздалегідь."
                    ),
                )
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
        booking.status = ApptBookingStatus.CANCELLED_BY_MASTER
        booking.cancel_reason = "Скасовано майстром з поверненням депозиту"
        if deposit:
            deposit.status = ApptDepositStatus.RETURNED
        await session.commit()
        await callback.answer("↩️ Скасовано, депозит повертається.")

        if client_tid:
            try:
                await bot.send_message(
                    chat_id=client_tid,
                    text=(
                        f"😔 Майстер скасував ваш запис.\n\n"
                        f"📅 {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n\n"
                        f"Депозит буде повернуто. Вибачте за незручності. /start"
                    ),
                )
            except Exception as e:
                logger.warning("Could not notify client about cancellation: %s", e)

        try:
            await callback.message.edit_text(
                f"Запис #{booking_id} скасовано. Депозит повертається клієнту.",
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

async def schedule_view(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
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
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])

    try:
        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[rows[0] + rows[1] + rows[2], rows[3] + rows[4], rows[5] + rows[6], rows[7]]),
        )
    except Exception:
        # Fallback simpler layout
        flat = [r[0] for r in rows[:-1]]
        combined = [flat[i:i+3] for i in range(0, len(flat), 3)]
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

    today = date.today()
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
            text=f"🎨 {w.style} | {w.price} | 👁{w.view_count}",
            callback_data=f"tttm_portfolio_del:{w.id}",
        )]
        for w in works
    ]
    rows.append([types.InlineKeyboardButton(text="➕ Додати роботу", callback_data="tttm_portfolio_add")])
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tttm_admin:home")])
    await callback.message.edit_text(
        f"🎨 <b>Портфоліо</b> ({count} робіт):\n\n"
        "Натисніть на роботу щоб <b>видалити</b>:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


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


async def portfolio_add_photo(message: types.Message, state: FSMContext) -> None:
    if not message.photo:
        await message.answer("Надішліть фото:")
        return
    await state.update_data(portfolio_photo_id=message.photo[-1].file_id)
    await message.answer("Введіть <b>стиль</b> (наприклад: Реалізм, Blackwork):")
    await state.set_state(TattooMasterFSM.portfolio_style)


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


# ── Admin home ────────────────────────────────────────────────────────────────

async def admin_home(
    callback: types.CallbackQuery,
) -> None:
    await callback.message.edit_text(
        "⚙️ <b>Панель майстра</b>\n\nОберіть розділ:",
        reply_markup=_admin_markup(),
    )
    await callback.answer()


# ── Handler registration ───────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    # Admin home
    dp.callback_query.register(admin_home, F.data == "tttm_admin:home")

    # Booking list + single booking
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

    # Blocked dates
    dp.callback_query.register(blocked_view,       F.data == "tttm_blocked")
    dp.callback_query.register(blocked_add_start,  F.data == "tttm_block_add")
    dp.callback_query.register(blocked_delete,     F.data.startswith("tttm_block_del:"))
    dp.message.register(blocked_date_start, TattooMasterFSM.block_date_start, F.text)
    dp.message.register(blocked_date_end,   TattooMasterFSM.block_date_end,   F.text)
    dp.message.register(blocked_reason,     TattooMasterFSM.block_reason,     F.text)

    # Settings
    dp.callback_query.register(settings_view, F.data == "tttm_settings")

    # Portfolio admin
    dp.callback_query.register(admin_portfolio,    F.data == "tttm_portfolio")
    dp.callback_query.register(portfolio_add_start, F.data == "tttm_portfolio_add")
    dp.callback_query.register(portfolio_delete,   F.data.startswith("tttm_portfolio_del:"))
    dp.message.register(portfolio_add_photo, TattooMasterFSM.portfolio_photo, F.photo)
    dp.message.register(portfolio_add_style, TattooMasterFSM.portfolio_style, F.text)
    dp.message.register(portfolio_add_desc,  TattooMasterFSM.portfolio_desc,  F.text)
    dp.message.register(portfolio_add_time,  TattooMasterFSM.portfolio_time,  F.text)
    dp.message.register(portfolio_add_price, TattooMasterFSM.portfolio_price, F.text)
