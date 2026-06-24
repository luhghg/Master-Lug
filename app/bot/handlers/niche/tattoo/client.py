"""Client-facing handlers for the TATTOO niche (v2 full booking lifecycle)."""
import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import (
    ApptBlockedDate, ApptBooking, ApptBookingStatus,
    ApptClient, ApptDeposit, ApptDepositStatus, ApptReminder, ApptSchedule,
    ReminderStatus, ReminderType,
)
from app.models.tattoo import TattooPortfolio, TattooReview, ReviewStatus, TattooService
from app.services.config_service import get_cfg, is_demo_bot

_TZ = ZoneInfo("Europe/Kyiv")

logger = logging.getLogger(__name__)

# ── Config keys ───────────────────────────────────────────────────────────────
_DEPOSIT_AMOUNT   = "ttt_deposit_amount"
_DEPOSIT_ENABLED  = "ttt_deposit_enabled"
_CARD_NUMBER      = "ttt_card_number"
_WELCOME_TEXT     = "ttt_welcome"
_SOCIAL_TEXT      = "ttt_social"
_DEPOSIT_MINUTES  = "ttt_deposit_minutes"  # minutes client has to pay before slot released

_DEFAULT_DEPOSIT   = 500
_DEFAULT_CARD      = ""
_DEFAULT_WELCOME   = "👋 <b>Ласкаво просимо!</b>\n\nОберіть що вас цікавить:"
_DEFAULT_SOCIAL    = "📱 Сторінки майстра поки не налаштовані."

# Calendar
_MONTHS_UA = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
]
_WEEKDAYS_UA = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

# Booking flow defaults
_STYLES = [
    "🖤 Реалізм",
    "⬛ Blackwork",
    "🎨 Акварель",
    "🔵 Дотворк",
    "📐 Геометрія",
    "🌸 Фіні лайн",
    "🏮 Японський",
    "🦋 Нео-традиція",
    "✏️ Інший стиль",
]
_ZONES = [
    "🦾 Рука / передпліччя",
    "💪 Плече / верхня рука",
    "⌚ Зап'ясток",
    "🦵 Стегно / литка",
    "🦶 Гомілка / стопа",
    "🔙 Спина / лопатка",
    "🤜 Ребра / бік",
    "🫀 Груди / ключиця",
    "📍 Шия",
    "⚡ Інше місце",
]
_SIZES = [
    ("🔹 Мінімалізм (до 5 см)", "tiny"),
    ("🔷 Компактний (5–15 см)", "small"),
    ("🟦 Середній (15–25 см)", "medium"),
    ("🟩 Великий (25+ см)", "large"),
    ("🗓 Тільки консультація", "consult"),
]


# ── FSM ───────────────────────────────────────────────────────────────────────

class TattooClientFSM(StatesGroup):
    style       = State()
    body_zone   = State()
    body_size   = State()
    reference   = State()
    allergy     = State()
    allergy_txt = State()
    overlap     = State()
    overlap_txt = State()
    pick_date   = State()
    pick_time   = State()
    screenshot  = State()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _home_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🏠 Меню", callback_data="ttt_menu:home")
    ]])


def _cancel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel")
    ]])


def _back_cancel_row(back_cd: str) -> list[types.InlineKeyboardButton]:
    return [
        types.InlineKeyboardButton(text="◀️ Назад", callback_data=back_cd),
        types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel"),
    ]


async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


async def _get_deposit(session: AsyncSession, bot_id: int) -> int:
    raw = await get_cfg(session, bot_id, _DEPOSIT_AMOUNT, str(_DEFAULT_DEPOSIT))
    try:
        return int(raw)
    except (ValueError, TypeError):
        return _DEFAULT_DEPOSIT


async def _get_deposit_enabled(session: AsyncSession, bot_id: int) -> bool:
    raw = await get_cfg(session, bot_id, _DEPOSIT_ENABLED, "true")
    return raw.lower() != "false"


async def _get_card(session: AsyncSession, bot_id: int) -> str:
    return await get_cfg(session, bot_id, _CARD_NUMBER, _DEFAULT_CARD)


async def _upsert_client(
    session: AsyncSession, bot_id: int, user: types.User
) -> ApptClient:
    stmt = (
        pg_insert(ApptClient)
        .values(
            bot_id=bot_id,
            telegram_id=user.id,
            username=user.username,
            full_name=user.full_name,
        )
        .on_conflict_do_update(
            constraint="uq_appt_client",
            set_={
                "username":  user.username,
                "full_name": user.full_name,
            },
        )
        .returning(ApptClient)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.scalar_one()


# ── Available slots calculation ───────────────────────────────────────────────

async def _available_dates(
    session: AsyncSession, bot_id: int, lookahead_days: int = 60
) -> set[date]:
    """Return set of dates that have at least one free slot."""
    schedules = (await session.execute(
        select(ApptSchedule).where(
            ApptSchedule.bot_id == bot_id,
            ApptSchedule.is_active.is_(True),
        )
    )).scalars().all()

    if not schedules:
        # No schedule configured — return next 30 weekdays as fallback
        result = set()
        d = datetime.now(_TZ).date() + timedelta(days=1)
        while len(result) < 30:
            if d.weekday() < 5:  # Mon-Fri
                result.add(d)
            d += timedelta(days=1)
        return result

    schedule_map = {s.day_of_week: s for s in schedules}

    blocked = (await session.execute(
        select(ApptBlockedDate).where(ApptBlockedDate.bot_id == bot_id)
    )).scalars().all()

    def _is_blocked(d: date) -> bool:
        for b in blocked:
            if b.date_start <= d <= b.date_end:
                return True
        return False

    # Get booked slots (AWAITING_DEPOSIT + CONFIRMED)
    booked_rows = (await session.execute(
        select(ApptBooking.slot_date, ApptBooking.slot_time).where(
            ApptBooking.bot_id == bot_id,
            ApptBooking.status.in_([
                ApptBookingStatus.AWAITING_DEPOSIT,
                ApptBookingStatus.CONFIRMED,
                ApptBookingStatus.PENDING,
            ]),
        )
    )).all()
    booked = {(r.slot_date, r.slot_time) for r in booked_rows}

    today = datetime.now(_TZ).date()
    available: set[date] = set()

    for offset in range(1, lookahead_days + 1):
        d = today + timedelta(days=offset)
        dow = d.weekday()
        if dow not in schedule_map:
            continue
        if _is_blocked(d):
            continue
        sched = schedule_map[dow]
        slots = _generate_slots(sched)
        free = [s for s in slots if (d, s) not in booked]
        if free:
            available.add(d)

    return available


def _generate_slots(sched: ApptSchedule) -> list[str]:
    """Generate "HH:MM" slots from schedule."""
    try:
        sh, sm = map(int, sched.start_time.split(":"))
        eh, em = map(int, sched.end_time.split(":"))
    except Exception:
        return []
    step = (sched.slot_duration_min or 60) + (sched.buffer_min or 0)
    start_min = sh * 60 + sm
    end_min   = eh * 60 + em
    slots = []
    cur = start_min
    while cur + (sched.slot_duration_min or 60) <= end_min:
        slots.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += step
    return slots


async def _slots_for_date(
    session: AsyncSession, bot_id: int, d: date
) -> list[str]:
    sched_row = (await session.execute(
        select(ApptSchedule).where(
            ApptSchedule.bot_id == bot_id,
            ApptSchedule.day_of_week == d.weekday(),
            ApptSchedule.is_active.is_(True),
        )
    )).scalar_one_or_none()

    if sched_row is None:
        # Default fallback slots if no schedule
        return ["10:00", "12:00", "14:00", "16:00", "18:00"]

    all_slots = _generate_slots(sched_row)

    booked_rows = (await session.execute(
        select(ApptBooking.slot_time).where(
            ApptBooking.bot_id == bot_id,
            ApptBooking.slot_date == d,
            ApptBooking.status.in_([
                ApptBookingStatus.AWAITING_DEPOSIT,
                ApptBookingStatus.CONFIRMED,
                ApptBookingStatus.PENDING,
            ]),
        )
    )).scalars().all()
    booked_times = set(booked_rows)

    now = datetime.now(_TZ)
    today = now.date()

    result = []
    for slot in all_slots:
        if slot in booked_times:
            continue
        if d == today:
            h, m = map(int, slot.split(":"))
            slot_dt = datetime(d.year, d.month, d.day, h, m, tzinfo=_TZ)
            if slot_dt <= now + timedelta(hours=1):
                continue
        result.append(slot)
    return result


# ── Calendar widget ───────────────────────────────────────────────────────────

def _make_ttt_calendar(
    year: int, month: int, available: set[date]
) -> types.InlineKeyboardMarkup:
    today = datetime.now(_TZ).date()
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1

    rows: list[list[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(text="◀️", callback_data=f"ttt_b_nav:{prev_y}:{prev_m:02d}"),
            types.InlineKeyboardButton(text=f"{_MONTHS_UA[month]} {year}", callback_data="ttt_ignore"),
            types.InlineKeyboardButton(text="▶️", callback_data=f"ttt_b_nav:{next_y}:{next_m:02d}"),
        ],
        [types.InlineKeyboardButton(text=d, callback_data="ttt_ignore") for d in _WEEKDAYS_UA],
    ]
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(types.InlineKeyboardButton(text=" ", callback_data="ttt_ignore"))
            else:
                d = date(year, month, day)
                if d <= today or d not in available:
                    row.append(types.InlineKeyboardButton(text="·", callback_data="ttt_ignore"))
                else:
                    row.append(types.InlineKeyboardButton(
                        text=f"✅{day}",
                        callback_data=f"ttt_b_day:{year}-{month:02d}-{day:02d}",
                    ))
        rows.append(row)

    rows.append([types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# ── Main menu ─────────────────────────────────────────────────────────────────

def _menu_markup() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="🎨 Портфоліо",  callback_data="ttt_menu:portfolio"),
            types.InlineKeyboardButton(text="📅 Записатись", callback_data="ttt_menu:booking"),
        ],
        [
            types.InlineKeyboardButton(text="⭐ Відгуки",    callback_data="ttt_menu:reviews"),
            types.InlineKeyboardButton(text="💰 Прайс",      callback_data="ttt_menu:price"),
        ],
        [
            types.InlineKeyboardButton(text="📱 Соцмережі",  callback_data="ttt_menu:social"),
        ],
    ])


async def show_client_menu(
    message: types.Message,
    session: AsyncSession | None = None,
    registered_bot_id: int = 0,
) -> None:
    text = _DEFAULT_WELCOME
    if session and registered_bot_id:
        text = await get_cfg(session, registered_bot_id, _WELCOME_TEXT, _DEFAULT_WELCOME)
    await message.answer(text, reply_markup=_menu_markup())


# ── Menu navigation ───────────────────────────────────────────────────────────

async def cmd_menu(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await state.clear()
    await show_client_menu(message, session, registered_bot_id)


async def menu_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    action = callback.data.split(":")[1]
    await callback.answer()

    if action == "home":
        text = await get_cfg(session, registered_bot_id, _WELCOME_TEXT, _DEFAULT_WELCOME)
        await _safe_edit(callback.message, text, reply_markup=_menu_markup())

    elif action == "portfolio":
        await _show_portfolio_categories(callback.message, session, registered_bot_id)

    elif action == "booking":
        await state.clear()
        await _start_booking(callback.message, state)

    elif action == "reviews":
        await _show_reviews(callback.message, session, registered_bot_id, 0)

    elif action == "price":
        await _show_price(callback.message, session, registered_bot_id)

    elif action == "social":
        text = await get_cfg(session, registered_bot_id, _SOCIAL_TEXT, _DEFAULT_SOCIAL)
        await _safe_edit(callback.message, text, reply_markup=_home_kb())


# ── Portfolio ─────────────────────────────────────────────────────────────────

async def _show_portfolio_categories(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    result = await session.execute(
        select(TattooPortfolio.style).where(TattooPortfolio.bot_id == bot_id).distinct()
    )
    styles = [r[0] for r in result.all()]
    if not styles:
        await _safe_edit(
            message,
            "😔 Портфоліо ще порожнє. Скоро з'явиться!",
            reply_markup=_home_kb(),
        )
        return
    rows = [
        [types.InlineKeyboardButton(text=s, callback_data=f"ttt_p_style:{s}")]
        for s in styles
    ]
    rows.append([types.InlineKeyboardButton(text="🏠 Меню", callback_data="ttt_menu:home")])
    await _safe_edit(
        message,
        "🎨 <b>Оберіть категорію:</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def portfolio_style(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    style = callback.data.split(":", 1)[1]
    await _show_portfolio_page(callback.message, session, registered_bot_id, style, 0, edit=False)
    await callback.answer()


async def portfolio_navigate(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    _, style, idx_str = callback.data.split(":", 2)
    await _show_portfolio_page(callback.message, session, registered_bot_id, style, int(idx_str), edit=True)
    await callback.answer()


async def _show_portfolio_page(
    message: types.Message, session: AsyncSession, bot_id: int,
    style: str, idx: int, edit: bool = False,
) -> None:
    result = await session.execute(
        select(TattooPortfolio)
        .where(TattooPortfolio.bot_id == bot_id, TattooPortfolio.style == style)
        .order_by(TattooPortfolio.created_at)
    )
    works = list(result.scalars().all())
    if not works:
        await message.answer(f"😔 У категорії <b>{style}</b> поки немає робіт.", reply_markup=_home_kb())
        return

    idx = max(0, min(idx, len(works) - 1))
    work = works[idx]
    work.view_count = (work.view_count or 0) + 1
    await session.commit()

    caption = (
        f"🎨 <b>{style}</b>  [{idx + 1}/{len(works)}]\n\n"
        f"📝 {work.description}\n"
        f"⏱ Час: {work.work_time}\n"
        f"💰 Ціна: {work.price}"
    )
    nav = []
    if idx > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"ttt_p_view:{style}:{idx - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{idx + 1}/{len(works)}", callback_data="ttt_ignore"))
    if idx < len(works) - 1:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"ttt_p_view:{style}:{idx + 1}"))

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text="🔥 Хочу такий стиль!", callback_data=f"ttt_p_want:{style}")],
        [types.InlineKeyboardButton(text="◀️ Стилі", callback_data="ttt_p_back")],
    ])

    if edit:
        try:
            await message.edit_media(
                media=types.InputMediaPhoto(media=work.photo_id, caption=caption, parse_mode="HTML"),
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    await message.answer_photo(photo=work.photo_id, caption=caption, reply_markup=kb)


async def portfolio_back(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _show_portfolio_categories(callback.message, session, registered_bot_id)
    await callback.answer()


async def portfolio_want(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    style = callback.data.split(":", 1)[1]
    await state.update_data(style=style)
    await callback.answer()
    await _ask_zone(callback.message, from_portfolio=True)


# ── Reviews ───────────────────────────────────────────────────────────────────

async def _show_reviews(
    message: types.Message, session: AsyncSession, bot_id: int, page: int
) -> None:
    result = await session.execute(
        select(TattooReview)
        .where(TattooReview.bot_id == bot_id, TattooReview.status == ReviewStatus.APPROVED)
        .order_by(TattooReview.created_at.desc())
        .offset(page * 5)
        .limit(6)
    )
    reviews = list(result.scalars().all())
    if not reviews and page == 0:
        await _safe_edit(message, "⭐ Відгуків поки немає.", reply_markup=_home_kb())
        return

    has_more = len(reviews) > 5
    reviews = reviews[:5]
    text = "⭐ <b>Відгуки клієнтів:</b>\n\n" + "\n\n".join(
        f"👤 <b>{r.user_name or 'Клієнт'}</b>\n{r.text}" for r in reviews
    )

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️ Раніше", callback_data=f"ttt_rev_nav:{page - 1}"))
    if has_more:
        nav.append(types.InlineKeyboardButton(text="Далі ➡️", callback_data=f"ttt_rev_nav:{page + 1}"))

    rows = [nav] if nav else []
    rows.append([types.InlineKeyboardButton(text="🏠 Меню", callback_data="ttt_menu:home")])
    await _safe_edit(message, text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))


async def reviews_navigate(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    page = int(callback.data.split(":")[1])
    await _show_reviews(callback.message, session, registered_bot_id, page)
    await callback.answer()


# ── Price list ────────────────────────────────────────────────────────────────

async def _show_price(
    message: types.Message, session: AsyncSession, bot_id: int
) -> None:
    result = await session.execute(
        select(TattooService)
        .where(TattooService.bot_id == bot_id)
        .order_by(TattooService.position)
    )
    services = list(result.scalars().all())
    if not services:
        await _safe_edit(message, "💰 Майстер ще не додав послуги.", reply_markup=_home_kb())
        return
    lines = [f"• <b>{s.name}</b> — {s.price}" for s in services]
    deposit = await _get_deposit(session, bot_id)
    text = "💰 <b>Прайс-лист:</b>\n\n" + "\n".join(lines)
    text += f"\n\n💳 Депозит для запису: <b>{deposit} грн</b>"
    await _safe_edit(message, text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📅 Записатись", callback_data="ttt_menu:booking")],
        [types.InlineKeyboardButton(text="🏠 Меню",       callback_data="ttt_menu:home")],
    ]))


# ── Booking flow ──────────────────────────────────────────────────────────────

async def _start_booking(message: types.Message, state: FSMContext) -> None:
    rows = [
        [types.InlineKeyboardButton(text=s, callback_data=f"ttt_book_style:{s}")]
        for s in _STYLES
    ]
    rows.append([types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel")])
    await _safe_edit(
        message,
        "📅 <b>Запис до майстра</b>\n\nКрок 1 — Оберіть стиль татуювання:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(TattooClientFSM.style)


async def book_style_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    style = callback.data.split(":", 1)[1]
    await state.update_data(style=style)
    await callback.answer()
    await _ask_zone(callback.message, from_portfolio=False)
    await state.set_state(TattooClientFSM.body_zone)


async def _ask_zone(message: types.Message, from_portfolio: bool = False) -> None:
    rows = [
        [types.InlineKeyboardButton(text=z, callback_data=f"ttt_book_zone:{z}")]
        for z in _ZONES
    ]
    if not from_portfolio:
        rows.append(_back_cancel_row("ttt_book:back_style"))
    else:
        rows.append([types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel")])
    await _safe_edit(
        message,
        "Крок 2 — Де будемо робити татуювання?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def book_zone_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    zone = callback.data.split(":", 1)[1]
    await state.update_data(body_zone=zone)
    await callback.answer()
    rows = [
        [types.InlineKeyboardButton(text=label, callback_data=f"ttt_book_size:{key}")]
        for label, key in _SIZES
    ]
    rows.append(_back_cancel_row("ttt_book:back_zone"))
    await _safe_edit(
        callback.message,
        "Крок 3 — Приблизний розмір:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(TattooClientFSM.body_size)


async def book_size_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    key = callback.data.split(":", 1)[1]
    label = next((lbl for lbl, k in _SIZES if k == key), key)
    await state.update_data(body_size=label)
    await callback.answer()
    await _ask_reference(callback.message)
    await state.set_state(TattooClientFSM.reference)


async def _ask_reference(message: types.Message) -> None:
    await _safe_edit(
        message,
        "Крок 4 — <b>Фото-референс</b>\n\n"
        "Надішліть фото (скриншот, Pinterest, інший зразок) або натисніть «Пропустити».",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⏭ Пропустити", callback_data="ttt_book_ref:skip")],
            _back_cancel_row("ttt_book:back_size"),
        ]),
    )


async def book_reference_photo(
    message: types.Message,
    state: FSMContext,
) -> None:
    current = await state.get_state()
    if current != TattooClientFSM.reference:
        return
    if not message.photo:
        await message.answer("Будь ласка, надішліть <b>фото</b> або натисніть «Пропустити».")
        return
    file_id = message.photo[-1].file_id
    await state.update_data(reference_file_id=file_id, reference_text="(фото)")
    await _ask_allergy(message)
    await state.set_state(TattooClientFSM.allergy)


async def book_reference_skip(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await state.update_data(reference_file_id=None, reference_text=None)
    await callback.answer()
    await _ask_allergy(callback.message)
    await state.set_state(TattooClientFSM.allergy)


async def _ask_allergy(message: types.Message) -> None:
    await _safe_edit(
        message,
        "Крок 5 — <b>Алергія або протипоказання?</b>\n\n"
        "Є алергія на латекс, фарбники, анестетики або медичні протипоказання?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Так, є", callback_data="ttt_book_allergy:yes")],
            [types.InlineKeyboardButton(text="🚫 Немає",  callback_data="ttt_book_allergy:no")],
            _back_cancel_row("ttt_book:back_ref"),
        ]),
    )


async def book_allergy_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    choice = callback.data.split(":")[1]
    await callback.answer()
    if choice == "yes":
        await _safe_edit(
            callback.message,
            "Опишіть алергію або протипоказання (коротко):",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                _back_cancel_row("ttt_book:back_allergy_choice"),
            ]),
        )
        await state.set_state(TattooClientFSM.allergy_txt)
    else:
        await state.update_data(allergy_text=None)
        await _ask_overlap(callback.message)
        await state.set_state(TattooClientFSM.overlap)


async def book_allergy_text(
    message: types.Message,
    state: FSMContext,
) -> None:
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("Будь ласка, опишіть детальніше.")
        return
    await state.update_data(allergy_text=message.text.strip())
    await _ask_overlap(message)
    await state.set_state(TattooClientFSM.overlap)


async def _ask_overlap(message: types.Message) -> None:
    await _safe_edit(
        message,
        "Крок 6 — <b>Перекриття?</b>\n\n"
        "Будемо перекривати існуюче татуювання або шрам?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Так", callback_data="ttt_book_overlap:yes")],
            [types.InlineKeyboardButton(text="🚫 Ні",  callback_data="ttt_book_overlap:no")],
            _back_cancel_row("ttt_book:back_overlap_choice"),
        ]),
    )


async def book_overlap_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    choice = callback.data.split(":")[1]
    await callback.answer()
    if choice == "yes":
        await _safe_edit(
            callback.message,
            "Коротко опишіть що перекриваємо:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                _back_cancel_row("ttt_book:back_overlap_choice"),
            ]),
        )
        await state.set_state(TattooClientFSM.overlap_txt)
    else:
        await state.update_data(overlap_text=None)
        await _ask_date(callback.message, state, session, registered_bot_id)


async def book_overlap_text(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("Будь ласка, опишіть детальніше.")
        return
    await state.update_data(overlap_text=message.text.strip())
    await _ask_date(message, state, session, registered_bot_id)


async def _ask_date(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot_id: int,
) -> None:
    available = await _available_dates(session, bot_id)
    today = datetime.now(_TZ).date()
    year, month = today.year, today.month
    cal = _make_ttt_calendar(year, month, available)
    await state.update_data(
        cal_year=year,
        cal_month=month,
        available=[d.isoformat() for d in available],
    )
    await _safe_edit(
        message,
        "Крок 7 — <b>Оберіть дату</b> (✅ — доступні дати):",
        reply_markup=cal,
    )
    await state.set_state(TattooClientFSM.pick_date)


async def calendar_navigate(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    _, year_str, month_str = callback.data.split(":")
    year, month = int(year_str), int(month_str)
    data = await state.get_data()
    available_iso = data.get("available", [])
    available = {date.fromisoformat(d) for d in available_iso}
    cal = _make_ttt_calendar(year, month, available)
    await state.update_data(cal_year=year, cal_month=month)
    try:
        await callback.message.edit_reply_markup(reply_markup=cal)
    except Exception:
        pass
    await callback.answer()


async def calendar_day_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    date_str = callback.data.split(":")[1]
    d = date.fromisoformat(date_str)
    await state.update_data(slot_date=date_str)

    slots = await _slots_for_date(session, registered_bot_id, d)
    if not slots:
        await callback.answer("😔 На цю дату немає вільних слотів, оберіть іншу.", show_alert=True)
        return

    rows = [
        [types.InlineKeyboardButton(text=s, callback_data=f"ttt_b_time:{s}")]
        for s in slots
    ]
    rows.append(_back_cancel_row("ttt_book:back_date"))
    day_ua = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"][d.weekday()]
    await _safe_edit(
        callback.message,
        f"Крок 8 — <b>Оберіть час</b>\n📅 {day_ua}, {d.strftime('%d.%m.%Y')}:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.set_state(TattooClientFSM.pick_time)
    await callback.answer()


async def time_picked(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    slot_time = callback.data.split(":", 1)[1]
    await state.update_data(slot_time=slot_time)
    data = await state.get_data()
    deposit = await _get_deposit(session, registered_bot_id)
    enabled = await _get_deposit_enabled(session, registered_bot_id)
    await callback.answer()
    await _show_summary(callback.message, data, deposit if enabled else None)


async def _show_summary(message: types.Message, data: dict, deposit: int | None) -> None:
    d = date.fromisoformat(data["slot_date"])
    day_ua = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"][d.weekday()]
    ref = "📎 Є фото" if data.get("reference_file_id") else "—"
    allergy = data.get("allergy_text") or "Немає"
    overlap = data.get("overlap_text") or "Немає"
    dep_line = f"💳 Депозит: <b>{deposit} грн</b>\n\n" if deposit is not None else ""

    text = (
        "📋 <b>Перевірте ваш запис:</b>\n\n"
        f"🎨 Стиль:       <b>{data.get('style', '—')}</b>\n"
        f"📍 Місце:       <b>{data.get('body_zone', '—')}</b>\n"
        f"📏 Розмір:      <b>{data.get('body_size', '—')}</b>\n"
        f"📎 Референс:   {ref}\n"
        f"💊 Алергія:    {allergy}\n"
        f"♻️ Перекриття: {overlap}\n\n"
        f"📅 Дата:  <b>{day_ua}, {d.strftime('%d.%m.%Y')}</b>\n"
        f"🕐 Час:   <b>{data.get('slot_time')}</b>\n\n"
        + dep_line
        + "Все вірно?"
    )
    await _safe_edit(
        message,
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Підтвердити", callback_data="ttt_book:confirm")],
            [types.InlineKeyboardButton(text="◀️ Змінити час",  callback_data="ttt_book:back_time")],
            [types.InlineKeyboardButton(text="❌ Скасувати",    callback_data="ttt_book:cancel")],
        ]),
    )


def _build_reminders(booking: ApptBooking) -> list[ApptReminder]:
    """Create ApptReminder rows for a newly confirmed booking."""
    slot_dt = datetime(
        booking.slot_date.year, booking.slot_date.month, booking.slot_date.day,
        *map(int, booking.slot_time.split(":")),
        tzinfo=_TZ,
    )
    now = datetime.now(_TZ)
    reminders = []
    for rtype, delta in [
        (ReminderType.HOURS_168, timedelta(days=7)),
        (ReminderType.HOURS_24,  timedelta(hours=24)),
        (ReminderType.HOURS_2,   timedelta(hours=2)),
    ]:
        scheduled = slot_dt - delta
        if scheduled > now:
            reminders.append(ApptReminder(
                booking_id=booking.id,
                reminder_type=rtype,
                status=ReminderStatus.PENDING,
                scheduled_at=scheduled.astimezone(timezone.utc),
            ))
    # Review reminder: 3 days after session
    reminders.append(ApptReminder(
        booking_id=booking.id,
        reminder_type=ReminderType.REVIEW,
        status=ReminderStatus.PENDING,
        scheduled_at=(slot_dt + timedelta(days=3)).astimezone(timezone.utc),
    ))
    return reminders


async def booking_confirm(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
    owner_telegram_id: int,
    bot: Bot,
) -> None:
    await callback.answer()
    data = await state.get_data()
    logger.info(
        "booking_confirm: bot=%s user=%s slot_date=%r slot_time=%r",
        registered_bot_id, callback.from_user.id,
        data.get("slot_date"), data.get("slot_time"),
    )
    if not data.get("slot_date"):
        await _safe_edit(
            callback.message,
            "⚠️ Сесія бронювання завершилась — почніть запис заново.",
            reply_markup=_home_kb(),
        )
        return
    user = callback.from_user

    try:
        client = await _upsert_client(session, registered_bot_id, user)
    except Exception:
        logger.exception("booking_confirm: _upsert_client failed bot=%s", registered_bot_id)
        await _safe_edit(callback.message, "⚠️ Помилка при оформленні запису. Спробуйте ще раз.", reply_markup=_home_kb())
        return
    d = date.fromisoformat(data["slot_date"])
    day_ua = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"][d.weekday()]
    deposit_enabled = await _get_deposit_enabled(session, registered_bot_id)

    if not deposit_enabled:
        # Auto-confirm: no deposit required
        booking = ApptBooking(
            bot_id=registered_bot_id,
            client_id=client.id,
            style=data.get("style"),
            body_zone=data.get("body_zone"),
            body_size=data.get("body_size"),
            reference_text=data.get("reference_text"),
            reference_file_id=data.get("reference_file_id"),
            allergy_text=data.get("allergy_text"),
            overlap_text=data.get("overlap_text"),
            slot_date=d,
            slot_time=data["slot_time"],
            status=ApptBookingStatus.CONFIRMED,
        )
        session.add(booking)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            await _safe_edit(
                callback.message,
                "⚡ <b>Цей час щойно зайняв інший клієнт.</b>\n\n"
                "Оберіть інший слот — ваша анкета збережена 👇",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🗓 Обрати інший час", callback_data="ttt_book:back_time")],
                    [types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel")],
                ]),
            )
            return
        client.bookings_count = (client.bookings_count or 0) + 1
        for rem in _build_reminders(booking):
            session.add(rem)
        await session.commit()
        await _safe_edit(
            callback.message,
            f"✅ <b>Ваш запис підтверджено!</b>\n\n"
            f"📅 {day_ua}, {d.strftime('%d.%m.%Y')} о {data['slot_time']}\n\n"
            f"Чекаємо вас! 🙏",
            reply_markup=_home_kb(),
        )
        await state.clear()
        await _notify_master_new_booking(
            bot, owner_telegram_id, booking, client, user, 0, registered_bot_id,
            deposit_enabled=False,
        )
        return

    # Deposit required — standard flow
    booking = ApptBooking(
        bot_id=registered_bot_id,
        client_id=client.id,
        style=data.get("style"),
        body_zone=data.get("body_zone"),
        body_size=data.get("body_size"),
        reference_text=data.get("reference_text"),
        reference_file_id=data.get("reference_file_id"),
        allergy_text=data.get("allergy_text"),
        overlap_text=data.get("overlap_text"),
        slot_date=d,
        slot_time=data["slot_time"],
        status=ApptBookingStatus.AWAITING_DEPOSIT,
    )
    session.add(booking)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        await _safe_edit(
            callback.message,
            "⚡ <b>Цей час щойно зайняв інший клієнт.</b>\n\n"
            "Оберіть інший слот — ваша анкета збережена 👇",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🗓 Обрати інший час", callback_data="ttt_book:back_time")],
                [types.InlineKeyboardButton(text="❌ Скасувати", callback_data="ttt_book:cancel")],
            ]),
        )
        return

    deposit_amount = await _get_deposit(session, registered_bot_id)
    deposit = ApptDeposit(
        booking_id=booking.id,
        amount=deposit_amount,
        status=ApptDepositStatus.WAITING,
    )
    session.add(deposit)
    client.bookings_count = (client.bookings_count or 0) + 1
    for rem in _build_reminders(booking):
        session.add(rem)
    await session.commit()

    await state.update_data(booking_id=booking.id)

    card = await _get_card(session, registered_bot_id)
    card_line = f"💳 Картка: <code>{card}</code>\n" if card else ""

    await _safe_edit(
        callback.message,
        f"✅ <b>Запис створено!</b>\n\n"
        f"📅 {day_ua}, {d.strftime('%d.%m.%Y')} о {data['slot_time']}\n\n"
        f"<b>Для підтвердження запису оплатіть депозит {deposit_amount} грн:</b>\n\n"
        f"{card_line}"
        f"💰 Сума: <b>{deposit_amount} грн</b>\n"
        f"📝 Призначення: <code>Депозит тату {d.strftime('%d.%m')}</code>\n\n"
        f"Після оплати надішліть сюди <b>скріншот переказу</b> 👇",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Скасувати запис", callback_data=f"ttt_book:self_cancel:{booking.id}")],
        ]),
    )
    await state.set_state(TattooClientFSM.screenshot)

    # Notify master
    await _notify_master_new_booking(
        bot, owner_telegram_id, booking, client, user, deposit_amount, registered_bot_id,
    )


async def _notify_master_new_booking(
    bot: Bot,
    owner_id: int,
    booking: ApptBooking,
    client: ApptClient,
    user: types.User,
    deposit: int,
    bot_id: int,
    deposit_enabled: bool = True,
) -> None:
    demo = is_demo_bot(bot_id)
    notify_id = user.id if demo else owner_id
    prefix = "📬 <b>[ДЕМО] Ось як виглядає повідомлення майстру:</b>\n\n" if demo else ""

    mention = f"@{user.username}" if user.username else user.full_name or str(user.id)
    day_ua = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"][booking.slot_date.weekday()]
    allergy = booking.allergy_text or "Немає"
    overlap = booking.overlap_text or "Немає"

    if deposit_enabled:
        dep_line = f"💳 Депозит: {deposit} грн — <b>очікуємо скріншот</b>"
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text="❌ Відхилити запис",
                callback_data=f"tttm_bk:{booking.id}:reject",
            )],
        ])
    else:
        dep_line = "💳 Без депозиту — ✅ <b>підтверджено автоматично</b>"
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text="📋 Переглянути запис",
                callback_data=f"tttm_bk:{booking.id}:view",
            )],
        ])

    text = (
        f"{prefix}"
        f"🔔 <b>Новий запис!</b>\n\n"
        f"👤 {mention}  (ID: {user.id})\n"
        f"🎨 {booking.style or '—'}\n"
        f"📍 {booking.body_zone or '—'}, {booking.body_size or '—'}\n"
        + (f"📎 Є фото-референс\n" if booking.reference_file_id else "")
        + f"💊 Алергія: {allergy}\n"
        f"♻️ Перекриття: {overlap}\n\n"
        f"📅 {day_ua}, {booking.slot_date.strftime('%d.%m.%Y')} о {booking.slot_time}\n"
        f"{dep_line}\n\n"
        f"ID запису: #{booking.id}"
    )
    try:
        await bot.send_message(chat_id=notify_id, text=text, reply_markup=kb)
    except Exception as e:
        logger.warning("Could not notify master about new booking: %s", e)


async def deposit_screenshot(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
    owner_telegram_id: int,
    bot: Bot,
) -> None:
    current = await state.get_state()
    if current != TattooClientFSM.screenshot:
        return
    if not message.photo:
        await message.answer("Будь ласка, надішліть <b>скріншот</b> (фото) оплати.")
        return

    data = await state.get_data()
    booking_id = data.get("booking_id")
    if not booking_id:
        await message.answer("Помилка: запис не знайдено. Спробуйте знову /start")
        return

    # Claim this screenshot immediately — second concurrent tap finds empty state
    await state.clear()

    # Guard: booking might have been cancelled by master while client was paying
    booking_check = await session.get(ApptBooking, booking_id)
    if not booking_check or booking_check.status != ApptBookingStatus.AWAITING_DEPOSIT:
        await message.answer(
            "⚠️ <b>Ваш запис було скасовано — скріншот не прийнято.</b>\n\n"
            "Запишіться знову через /start",
            reply_markup=_home_kb(),
        )
        return

    file_id = message.photo[-1].file_id
    now = datetime.now(timezone.utc)

    deposit = (await session.execute(
        select(ApptDeposit).where(ApptDeposit.booking_id == booking_id)
    )).scalar_one_or_none()
    if deposit:
        deposit.screenshot_file_id = file_id
        deposit.status = ApptDepositStatus.SCREENSHOT_SENT
        deposit.paid_at = now
        await session.commit()

    await message.answer(
        "✅ <b>Скріншот отримано!</b>\n\n"
        "Майстер перевірить оплату і підтвердить запис — зазвичай протягом кількох годин.\n\n"
        "Очікуйте підтвердження 🙏",
        reply_markup=_home_kb(),
    )

    # Notify master with screenshot
    user = message.from_user
    demo = is_demo_bot(registered_bot_id)
    notify_id = user.id if demo else owner_telegram_id
    mention = f"@{user.username}" if user.username else user.full_name or str(user.id)
    prefix = "📬 <b>[ДЕМО] Повідомлення майстру:</b>\n\n" if demo else ""

    try:
        await bot.send_photo(
            chat_id=notify_id,
            photo=file_id,
            caption=(
                f"{prefix}"
                f"📸 <b>{mention} надіслав скріншот депозиту!</b>\n\n"
                f"Запис #{booking_id}"
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Підтвердити оплату",
                        callback_data=f"tttm_bk:{booking_id}:confirm_deposit",
                    ),
                    types.InlineKeyboardButton(
                        text="❌ Відхилити",
                        callback_data=f"tttm_bk:{booking_id}:reject",
                    ),
                ],
            ]),
        )
    except Exception as e:
        logger.warning("Could not send screenshot to master: %s", e)


# ── Back navigation ───────────────────────────────────────────────────────────

async def book_back(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    action = callback.data.split(":")[1]
    await callback.answer()

    if action == "cancel":
        await state.clear()
        text = await get_cfg(session, registered_bot_id, _WELCOME_TEXT, _DEFAULT_WELCOME)
        await _safe_edit(callback.message, text, reply_markup=_menu_markup())

    elif action == "back_style":
        await _start_booking(callback.message, state)
        await state.set_state(TattooClientFSM.style)

    elif action == "back_zone":
        data = await state.get_data()
        await state.update_data(body_zone=None)
        await _ask_zone(callback.message)
        await state.set_state(TattooClientFSM.body_zone)

    elif action == "back_size":
        data = await state.get_data()
        await state.update_data(body_size=None)
        await _ask_reference(callback.message)
        # go back to size step
        rows = [
            [types.InlineKeyboardButton(text=label, callback_data=f"ttt_book_size:{key}")]
            for label, key in _SIZES
        ]
        rows.append(_back_cancel_row("ttt_book:back_zone"))
        await _safe_edit(
            callback.message,
            "Крок 3 — Приблизний розмір:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await state.set_state(TattooClientFSM.body_size)

    elif action == "back_ref":
        await _ask_reference(callback.message)
        await state.set_state(TattooClientFSM.reference)

    elif action == "back_allergy_choice":
        await _ask_allergy(callback.message)
        await state.set_state(TattooClientFSM.allergy)

    elif action == "back_overlap_choice":
        await _ask_overlap(callback.message)
        await state.set_state(TattooClientFSM.overlap)

    elif action == "back_date":
        await _ask_date(callback.message, state, session, registered_bot_id)

    elif action == "back_time":
        data = await state.get_data()
        date_str = data.get("slot_date")
        if date_str:
            d = date.fromisoformat(date_str)
            slots = await _slots_for_date(session, registered_bot_id, d)
            rows = [
                [types.InlineKeyboardButton(text=s, callback_data=f"ttt_b_time:{s}")]
                for s in slots
            ]
            rows.append(_back_cancel_row("ttt_book:back_date"))
            day_ua = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"][d.weekday()]
            await _safe_edit(
                callback.message,
                f"Крок 8 — <b>Оберіть час</b>\n📅 {day_ua}, {d.strftime('%d.%m.%Y')}:",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
            )
            await state.set_state(TattooClientFSM.pick_time)


async def client_self_cancel(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    parts = callback.data.split(":")
    booking_id = int(parts[2])

    booking = await session.get(ApptBooking, booking_id)
    if booking and booking.bot_id == registered_bot_id:
        booking.status = ApptBookingStatus.CANCELLED_BY_CLIENT
        booking.cancel_reason = "Скасовано клієнтом"
        dep = (await session.execute(
            select(ApptDeposit).where(ApptDeposit.booking_id == booking_id)
        )).scalar_one_or_none()
        if dep:
            dep.status = ApptDepositStatus.RETURNED
        await session.commit()

    await state.clear()
    await callback.answer("Запис скасовано.")
    text = await get_cfg(session, registered_bot_id, _WELCOME_TEXT, _DEFAULT_WELCOME)
    await _safe_edit(callback.message, text, reply_markup=_menu_markup())


# ── Handler registration ───────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    # Menu
    dp.callback_query.register(menu_callback, F.data.startswith("ttt_menu:"))

    # Portfolio
    dp.callback_query.register(portfolio_style,    F.data.startswith("ttt_p_style:"))
    dp.callback_query.register(portfolio_navigate, F.data.startswith("ttt_p_view:"))
    dp.callback_query.register(portfolio_back,     F.data == "ttt_p_back")
    dp.callback_query.register(portfolio_want,     F.data.startswith("ttt_p_want:"))

    # Reviews
    dp.callback_query.register(reviews_navigate, F.data.startswith("ttt_rev_nav:"))

    # Booking flow — callbacks
    dp.callback_query.register(book_style_picked,   F.data.startswith("ttt_book_style:"))
    dp.callback_query.register(book_zone_picked,    F.data.startswith("ttt_book_zone:"))
    dp.callback_query.register(book_size_picked,    F.data.startswith("ttt_book_size:"))
    dp.callback_query.register(book_reference_skip, F.data == "ttt_book_ref:skip")
    dp.callback_query.register(book_allergy_picked, F.data.startswith("ttt_book_allergy:"))
    dp.callback_query.register(book_overlap_picked, F.data.startswith("ttt_book_overlap:"))
    dp.callback_query.register(calendar_navigate,   F.data.startswith("ttt_b_nav:"))
    dp.callback_query.register(calendar_day_picked, F.data.startswith("ttt_b_day:"))
    dp.callback_query.register(time_picked,         F.data.startswith("ttt_b_time:"))
    dp.callback_query.register(booking_confirm,     F.data == "ttt_book:confirm")
    dp.callback_query.register(client_self_cancel,  F.data.startswith("ttt_book:self_cancel:"))
    dp.callback_query.register(book_back,           F.data.startswith("ttt_book:"))

    # Booking flow — text/photo messages
    dp.message.register(
        book_reference_photo,
        TattooClientFSM.reference,
        F.photo,
    )
    dp.message.register(
        book_allergy_text,
        TattooClientFSM.allergy_txt,
        F.text,
    )
    dp.message.register(
        book_overlap_text,
        TattooClientFSM.overlap_txt,
        F.text,
    )
    dp.message.register(
        deposit_screenshot,
        TattooClientFSM.screenshot,
        F.photo,
    )

    # Ignore calendar header cells
    dp.callback_query.register(
        lambda c: c.answer(), F.data == "ttt_ignore"
    )
