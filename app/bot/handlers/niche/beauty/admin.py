"""Admin-facing handlers for the Beauty (tattoo) niche."""
import logging
import uuid
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers.niche.beauty.calendar_widget import make_calendar
from app.bot.handlers.niche.beauty.config import (
    MAX_TEXT_LEN, TIME_SLOTS as DEFAULT_SLOTS, DEFAULT_CATEGORIES,
)
from app.models.tattoo import (
    BookingStatus, BotSubscription, ReviewStatus,
    TattooBooking, TattooPortfolio, TattooReview,
)
from app.services.config_service import (
    CATEGORIES, SOCIAL_TEXT, TIME_SLOTS, WELCOME_TEXT,
    get_cfg, get_json, set_cfg, set_json,
)

logger = logging.getLogger(__name__)


# ── FSM ───────────────────────────────────────────────────────────────────────

class AddPortfolioFSM(StatesGroup):
    photo       = State()
    style       = State()
    description = State()
    work_time   = State()
    price       = State()


class BroadcastFSM(StatesGroup):
    message = State()
    confirm = State()


class CancelBookingFSM(StatesGroup):
    reason = State()


class SettingsFSM(StatesGroup):
    social   = State()
    welcome  = State()
    slots    = State()


class CategoryFSM(StatesGroup):
    add_name = State()


# ── UI helpers ────────────────────────────────────────────────────────────────

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


def _admin_menu_markup() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="➕ Додати роботу",  callback_data="tt_adm:add_portfolio"),
                types.InlineKeyboardButton(text="🗂 Портфоліо",       callback_data="tt_adm:portfolio"),
            ],
            [
                types.InlineKeyboardButton(text="📅 Розклад",         callback_data="tt_adm:schedule"),
                types.InlineKeyboardButton(text="⭐️ Відгуки",        callback_data="tt_adm:reviews"),
            ],
            [
                types.InlineKeyboardButton(text="📣 Розсилка",        callback_data="tt_adm:broadcast"),
                types.InlineKeyboardButton(text="⚙️ Налаштування",    callback_data="tt_adm:settings"),
            ],
            [
                types.InlineKeyboardButton(text="📊 Статистика",      callback_data="tt_adm:stats"),
            ],
        ]
    )


def _back_menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="◀️ Меню", callback_data="tt_adm:home")
    ]])


# ── Admin main menu ───────────────────────────────────────────────────────────

async def show_admin_menu(message: types.Message) -> None:
    await message.answer("🎨 <b>Адмін-панель студії</b>", reply_markup=_admin_menu_markup())


async def cmd_menu_admin(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await show_admin_menu(message)


async def admin_menu_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    action = callback.data.split(":")[1]
    await callback.answer()
    msg = callback.message

    if action == "home":
        await _safe_edit(msg, "🎨 <b>Адмін-панель студії</b>", reply_markup=_admin_menu_markup())
    elif action == "add_portfolio":
        await _portfolio_add_start(msg, state)
    elif action == "portfolio":
        await _admin_portfolio_list(msg, session, registered_bot_id)
    elif action == "schedule":
        await _schedule_show(msg, session, registered_bot_id)
    elif action == "reviews":
        await _admin_reviews_pending(msg, session, registered_bot_id)
    elif action == "broadcast":
        await _broadcast_start(msg, state)
    elif action == "settings":
        await _settings_menu(msg, session, registered_bot_id)
    elif action == "stats":
        await _admin_stats(msg, session, registered_bot_id)


# ── Stats ─────────────────────────────────────────────────────────────────────

async def _admin_stats(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    subscribers = (await session.execute(
        select(func.count(BotSubscription.id)).where(BotSubscription.bot_id == bot_id)
    )).scalar_one()

    portfolio_count = (await session.execute(
        select(func.count(TattooPortfolio.id)).where(TattooPortfolio.bot_id == bot_id)
    )).scalar_one()

    total_views = (await session.execute(
        select(func.sum(TattooPortfolio.view_count)).where(TattooPortfolio.bot_id == bot_id)
    )).scalar_one() or 0

    total_bookings = (await session.execute(
        select(func.count(TattooBooking.id)).where(TattooBooking.bot_id == bot_id)
    )).scalar_one()

    active_bookings = (await session.execute(
        select(func.count(TattooBooking.id)).where(
            TattooBooking.bot_id == bot_id,
            TattooBooking.status == BookingStatus.NEW,
        )
    )).scalar_one()

    approved_reviews = (await session.execute(
        select(func.count(TattooReview.id)).where(
            TattooReview.bot_id == bot_id,
            TattooReview.status == ReviewStatus.APPROVED,
        )
    )).scalar_one()

    await _safe_edit(
        message,
        f"📊 <b>Статистика студії</b>\n\n"
        f"👥 Підписників: <b>{subscribers}</b>\n\n"
        f"🖼 Портфоліо: <b>{portfolio_count}</b> робіт\n"
        f"👁 Переглядів усього: <b>{total_views}</b>\n\n"
        f"📅 Записів усього: <b>{total_bookings}</b>\n"
        f"✅ Активних: <b>{active_bookings}</b>\n\n"
        f"⭐️ Відгуків схвалено: <b>{approved_reviews}</b>",
        reply_markup=_back_menu_kb(),
    )


# ── Settings menu ─────────────────────────────────────────────────────────────

async def _settings_menu(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    await _safe_edit(
        message,
        "⚙️ <b>Налаштування бота</b>\n\nОберіть що хочете змінити:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="📱 Контакти / Соцмережі", callback_data="tt_cfg:social")],
                [types.InlineKeyboardButton(text="👋 Привітання клієнтів",  callback_data="tt_cfg:welcome")],
                [types.InlineKeyboardButton(text="🕐 Час роботи (слоти)",   callback_data="tt_cfg:slots")],
                [types.InlineKeyboardButton(text="📂 Категорії портфоліо",  callback_data="tt_cfg:categories")],
                [types.InlineKeyboardButton(text="◀️ Меню",                 callback_data="tt_adm:home")],
            ]
        ),
    )


async def settings_callback(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    action = callback.data.split(":")[1]
    await callback.answer()

    if action == "social":
        current = await get_cfg(session, registered_bot_id, SOCIAL_TEXT, "(не задано)")
        await _safe_edit(
            callback.message,
            f"📱 <b>Поточний текст контактів:</b>\n\n{current}\n\n"
            "Надішліть новий текст (підтримує HTML: <b>жирний</b>, <i>курсив</i>):",
            reply_markup=_cancel_kb(),
        )
        await state.set_state(SettingsFSM.social)

    elif action == "welcome":
        current = await get_cfg(session, registered_bot_id, WELCOME_TEXT, "(стандартне)")
        await _safe_edit(
            callback.message,
            f"👋 <b>Поточне привітання:</b>\n\n{current}\n\n"
            "Надішліть новий текст привітання:",
            reply_markup=_cancel_kb(),
        )
        await state.set_state(SettingsFSM.welcome)

    elif action == "slots":
        current = await get_json(session, registered_bot_id, TIME_SLOTS, DEFAULT_SLOTS)
        slots_text = ", ".join(current)
        await _safe_edit(
            callback.message,
            f"🕐 <b>Поточні слоти:</b> {slots_text}\n\n"
            "Надішліть нові слоти через кому в форматі <code>10:00, 12:00, 14:00, 16:00</code>:",
            reply_markup=_cancel_kb(),
        )
        await state.set_state(SettingsFSM.slots)

    elif action == "categories":
        await _categories_menu(callback.message, session, registered_bot_id)


async def settings_got_social(
    message: types.Message, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, SOCIAL_TEXT, message.text.strip())
    await state.clear()
    await message.answer("✅ Текст контактів оновлено!", reply_markup=_back_menu_kb())


async def settings_got_welcome(
    message: types.Message, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, WELCOME_TEXT, message.text.strip())
    await state.clear()
    await message.answer("✅ Текст привітання оновлено!", reply_markup=_back_menu_kb())


async def settings_got_slots(
    message: types.Message, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    raw = message.text.strip()
    slots = [s.strip() for s in raw.split(",") if s.strip()]
    valid = []
    for s in slots:
        try:
            datetime.strptime(s, "%H:%M")
            valid.append(s)
        except ValueError:
            pass
    if not valid:
        await message.answer("❌ Жоден слот не розпізнано. Формат: <code>10:00, 12:00, 14:00</code>")
        return
    await set_json(session, registered_bot_id, TIME_SLOTS, valid)
    await state.clear()
    await message.answer(f"✅ Слоти оновлено: {', '.join(valid)}", reply_markup=_back_menu_kb())


# ── Categories management ─────────────────────────────────────────────────────

async def _categories_menu(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    cats: list = await get_json(session, bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    result = await session.execute(
        select(TattooPortfolio.style, func.count(TattooPortfolio.id).label("cnt"))
        .where(TattooPortfolio.bot_id == bot_id)
        .group_by(TattooPortfolio.style)
    )
    counts = {row.style: row.cnt for row in result.all()}

    rows = []
    for c in cats:
        cnt = counts.get(c["key"], 0)
        rows.append([types.InlineKeyboardButton(
            text=f"{c['name']} ({cnt})",
            callback_data=f"tt_cat_del:{c['key']}",
        )])
    rows.append([types.InlineKeyboardButton(text="➕ Додати категорію", callback_data="tt_cat_add")])
    rows.append([types.InlineKeyboardButton(text="◀️ Назад",            callback_data="tt_adm:settings")])
    await _safe_edit(
        message,
        "📂 <b>Категорії портфоліо</b>\n\n"
        "Натисніть на категорію щоб <b>видалити</b> її.\n"
        "<i>Категорію з роботами видалити не можна.</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def category_add_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _safe_edit(
        callback.message,
        "➕ Введіть назву нової категорії (можна з емодзі):\n"
        "Наприклад: <code>🐉 Японський стиль</code>",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(CategoryFSM.add_name)
    await callback.answer()


async def category_got_name(
    message: types.Message, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer("❌ Назва має бути від 2 до 50 символів.")
        return
    cats: list = await get_json(session, registered_bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    key = f"cat_{uuid.uuid4().hex[:8]}"
    cats.append({"key": key, "name": name})
    await set_json(session, registered_bot_id, CATEGORIES, cats)
    await state.clear()
    await message.answer(
        f"✅ Категорію <b>{name}</b> додано!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="📂 Назад до категорій", callback_data="tt_cfg:categories"),
            types.InlineKeyboardButton(text="◀️ Меню",               callback_data="tt_adm:home"),
        ]]),
    )


async def category_delete(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    cat_key = callback.data.split(":")[1]
    count_res = await session.execute(
        select(func.count(TattooPortfolio.id)).where(
            TattooPortfolio.bot_id == registered_bot_id,
            TattooPortfolio.style == cat_key,
        )
    )
    if count_res.scalar_one() > 0:
        await callback.answer("❌ Спочатку видаліть всі роботи з цієї категорії!", show_alert=True)
        return

    cats: list = await get_json(session, registered_bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    cats = [c for c in cats if c["key"] != cat_key]
    await set_json(session, registered_bot_id, CATEGORIES, cats)
    await callback.answer("🗑 Категорію видалено", show_alert=True)
    await _categories_menu(callback.message, session, registered_bot_id)


# ── Add Portfolio FSM ─────────────────────────────────────────────────────────

async def _portfolio_add_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(message, "📸 Крок 1/5 — Надішліть фото роботи:", reply_markup=_cancel_kb())
    await state.set_state(AddPortfolioFSM.photo)


async def portfolio_got_photo(
    message: types.Message, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    if not message.photo:
        await message.answer("❌ Надішліть фото.")
        return
    await state.update_data(photo_id=message.photo[-1].file_id)
    cats: list = await get_json(session, registered_bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    await message.answer(
        "Крок 2/5 — Оберіть категорію:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=c["name"], callback_data=f"tt_adm_style:{c['key']}")]
            for c in cats
        ]),
    )
    await state.set_state(AddPortfolioFSM.style)


async def portfolio_got_style(callback: types.CallbackQuery, state: FSMContext) -> None:
    style_key = callback.data.split(":")[1]
    await state.update_data(style=style_key)
    await _safe_edit(callback.message, "Крок 3/5 — Опишіть роботу:", reply_markup=_cancel_kb())
    await state.set_state(AddPortfolioFSM.description)
    await callback.answer()


async def portfolio_got_description(message: types.Message, state: FSMContext) -> None:
    if not (2 <= len(message.text.strip()) <= MAX_TEXT_LEN):
        await message.answer(f"❌ Введіть від 2 до {MAX_TEXT_LEN} символів.")
        return
    await state.update_data(description=message.text.strip())
    await message.answer("Крок 4/5 — Час роботи (наприклад: 3 год, 2 сеанси):", reply_markup=_cancel_kb())
    await state.set_state(AddPortfolioFSM.work_time)


async def portfolio_got_work_time(message: types.Message, state: FSMContext) -> None:
    await state.update_data(work_time=message.text.strip())
    await message.answer("Крок 5/5 — Ціна (наприклад: від 2000 грн, договірна):", reply_markup=_cancel_kb())
    await state.set_state(AddPortfolioFSM.price)


async def portfolio_got_price(
    message: types.Message, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    data = await state.get_data()
    work = TattooPortfolio(
        bot_id=registered_bot_id,
        style=data["style"],
        photo_id=data["photo_id"],
        description=data["description"],
        work_time=data["work_time"],
        price=message.text.strip(),
    )
    session.add(work)
    await session.commit()
    cats: list = await get_json(session, registered_bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    cat_name = next((c["name"] for c in cats if c["key"] == data["style"]), data["style"])
    await state.clear()
    await message.answer(
        f"✅ Роботу додано до портфоліо! ({cat_name})",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="➕ Ще роботу", callback_data="tt_adm:add_portfolio"),
            types.InlineKeyboardButton(text="◀️ Меню",      callback_data="tt_adm:home"),
        ]]),
    )


# ── Admin portfolio list with delete ─────────────────────────────────────────

async def _admin_portfolio_list(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    result = await session.execute(
        select(TattooPortfolio.style, func.count(TattooPortfolio.id).label("cnt"))
        .where(TattooPortfolio.bot_id == bot_id)
        .group_by(TattooPortfolio.style)
    )
    rows = result.all()
    if not rows:
        await _safe_edit(message, "😔 Портфоліо порожнє.", reply_markup=_back_menu_kb())
        return

    cats: list = await get_json(session, bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    cat_map = {c["key"]: c["name"] for c in cats}

    kb_rows = [[types.InlineKeyboardButton(
        text=f"{cat_map.get(r.style, r.style)} ({r.cnt})",
        callback_data=f"tt_adm_plist:{r.style}:0",
    )] for r in rows]
    kb_rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tt_adm:home")])
    await _safe_edit(
        message,
        "🗂 <b>Портфоліо — оберіть категорію для перегляду/видалення:</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


async def admin_portfolio_browse(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    _, style_key, idx_str = callback.data.split(":")
    idx = int(idx_str)
    result = await session.execute(
        select(TattooPortfolio)
        .where(TattooPortfolio.bot_id == registered_bot_id, TattooPortfolio.style == style_key)
        .order_by(TattooPortfolio.created_at)
    )
    works = list(result.scalars().all())
    if not works:
        await _safe_edit(callback.message, "😔 Робіт у цій категорії немає.", reply_markup=_back_menu_kb())
        await callback.answer()
        return

    cats: list = await get_json(session, registered_bot_id, CATEGORIES, DEFAULT_CATEGORIES)
    idx = max(0, min(idx, len(works) - 1))
    work = works[idx]
    label = next((c["name"] for c in cats if c["key"] == style_key), style_key)
    caption = (
        f"🎨 <b>{label}</b> [{idx + 1}/{len(works)}]\n\n"
        f"📝 {work.description}\n⏱ {work.work_time}\n💰 {work.price}\n"
        f"👁 Переглядів: {work.view_count or 0}"
    )
    nav = []
    if idx > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"tt_adm_plist:{style_key}:{idx - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{idx + 1}/{len(works)}", callback_data="tt_ignore"))
    if idx < len(works) - 1:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"tt_adm_plist:{style_key}:{idx + 1}"))

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text="🗑 Видалити цю роботу", callback_data=f"tt_adm_pdel:{work.id}:{style_key}:{idx}")],
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="tt_adm:portfolio")],
    ])

    is_photo = bool(callback.message.photo)
    if is_photo:
        try:
            await callback.message.edit_media(
                media=types.InputMediaPhoto(media=work.photo_id, caption=caption, parse_mode="HTML"),
                reply_markup=kb,
            )
            await callback.answer()
            return
        except Exception:
            pass
    await callback.message.answer_photo(photo=work.photo_id, caption=caption, reply_markup=kb)
    await callback.answer()


async def admin_portfolio_delete(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    parts = callback.data.split(":")
    work_id, style_key, idx_str = int(parts[1]), parts[2], int(parts[3])
    work = await session.get(TattooPortfolio, work_id)
    if work and work.bot_id == registered_bot_id:
        await session.delete(work)
        await session.commit()
        await callback.answer("🗑 Роботу видалено", show_alert=True)
    else:
        await callback.answer("❌ Не знайдено", show_alert=True)
        return
    result = await session.execute(
        select(TattooPortfolio)
        .where(TattooPortfolio.bot_id == registered_bot_id, TattooPortfolio.style == style_key)
        .order_by(TattooPortfolio.created_at)
    )
    works = list(result.scalars().all())
    if not works:
        await callback.message.answer("😔 У цій категорії більше немає робіт.")
    else:
        new_idx = min(idx_str, len(works) - 1)
        callback.data = f"tt_adm_plist:{style_key}:{new_idx}"
        await admin_portfolio_browse(callback, session, registered_bot_id)


# ── Schedule ──────────────────────────────────────────────────────────────────

async def _schedule_show(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    today = date.today()
    marked = await _marked_days(session, bot_id, today.year, today.month)
    kb = make_calendar(today.year, today.month, "tt_adm_nav", "tt_adm_day", marked_days=marked)
    kb.inline_keyboard.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tt_adm:home")])
    await _safe_edit(message, "📅 <b>Розклад — оберіть день (📌 є записи):</b>", reply_markup=kb)


async def schedule_nav(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    _, year_s, month_s = callback.data.split(":")
    year, month = int(year_s), int(month_s)
    marked = await _marked_days(session, registered_bot_id, year, month)
    kb = make_calendar(year, month, "tt_adm_nav", "tt_adm_day", marked_days=marked)
    kb.inline_keyboard.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="tt_adm:home")])
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()


async def schedule_day(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    date_str = callback.data.split(":")[1]
    result = await session.execute(
        select(TattooBooking)
        .where(
            TattooBooking.bot_id == registered_bot_id,
            TattooBooking.date == date_str,
            TattooBooking.status == BookingStatus.NEW,
        )
        .order_by(TattooBooking.time_slot)
    )
    bookings = list(result.scalars().all())
    d = datetime.strptime(date_str, "%Y-%m-%d").date()

    if not bookings:
        await callback.message.answer(
            f"📅 {d.strftime('%d.%m.%Y')} — записів немає.",
            reply_markup=_back_menu_kb(),
        )
        await callback.answer()
        return

    await callback.message.answer(f"📅 <b>Записи на {d.strftime('%d.%m.%Y')}:</b>")
    for b in bookings:
        await callback.message.answer(
            f"⏰ <b>{b.time_slot}</b>\n"
            f"👤 ID: {b.user_id} | 📱 {b.phone or '—'}\n"
            f"💡 {b.idea}\n"
            f"📍 {b.body_part} | 📐 {b.size}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(
                    text="❌ Скасувати запис",
                    callback_data=f"tt_adm_cancel_book:{b.id}",
                )
            ]]),
        )
    await callback.answer()


async def admin_cancel_booking_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    booking_id = int(callback.data.split(":")[1])
    await state.update_data(cancel_booking_id=booking_id)
    await _safe_edit(
        callback.message,
        "✍️ Вкажіть причину скасування (буде надіслана клієнту):",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(CancelBookingFSM.reason)
    await callback.answer()


async def admin_cancel_booking_reason(
    message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot,
) -> None:
    data = await state.get_data()
    booking = await session.get(TattooBooking, data.get("cancel_booking_id"))
    if not booking:
        await message.answer("❌ Запис не знайдено.")
        await state.clear()
        return

    booking.status = BookingStatus.CANCELLED
    booking.cancel_reason = message.text.strip()
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Запис скасовано. Сповіщення надіслано клієнту.",
        reply_markup=_back_menu_kb(),
    )

    d = datetime.strptime(booking.date, "%Y-%m-%d").date()
    try:
        await bot.send_message(
            chat_id=booking.user_id,
            text=(
                f"❌ <b>Ваш запис скасовано майстром</b>\n\n"
                f"📅 {d.strftime('%d.%m.%Y')} о {booking.time_slot}\n\n"
                f"📝 Причина: {booking.cancel_reason}\n\n"
                f"Щоб записатись знову — натисніть /start"
            ),
        )
    except Exception:
        logger.warning("Could not notify client about cancellation")


# ── Review moderation ─────────────────────────────────────────────────────────

async def _admin_reviews_pending(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    result = await session.execute(
        select(TattooReview)
        .where(TattooReview.bot_id == bot_id, TattooReview.status == ReviewStatus.PENDING)
        .order_by(TattooReview.created_at)
    )
    pending = list(result.scalars().all())
    if not pending:
        await _safe_edit(message, "✅ Немає відгуків на модерацію.", reply_markup=_back_menu_kb())
        return

    await _safe_edit(
        message,
        f"⭐️ Відгуків на модерацію: <b>{len(pending)}</b>",
        reply_markup=_back_menu_kb(),
    )
    for r in pending[:10]:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="✅ Схвалити", callback_data=f"tt_ra_approve:{r.id}"),
            types.InlineKeyboardButton(text="❌ Видалити", callback_data=f"tt_ra_delete:{r.id}"),
        ]])
        text = f"👤 {r.user_name or 'Анонім'}\n\n{r.text}"
        if r.photo_id:
            await message.answer_photo(photo=r.photo_id, caption=text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)


async def review_approve(callback: types.CallbackQuery, session: AsyncSession) -> None:
    review_id = int(callback.data.split(":")[1])
    review = await session.get(TattooReview, review_id)
    if review:
        review.status = ReviewStatus.APPROVED
        await session.commit()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("✅ Схвалено")
    else:
        await callback.answer("❌ Відгук не знайдено")


async def review_delete(callback: types.CallbackQuery, session: AsyncSession) -> None:
    review_id = int(callback.data.split(":")[1])
    review = await session.get(TattooReview, review_id)
    if review:
        review.status = ReviewStatus.DELETED
        await session.commit()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("🗑 Видалено")
    else:
        await callback.answer("❌ Відгук не знайдено")


# ── Broadcast FSM ─────────────────────────────────────────────────────────────

async def _broadcast_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(
        message,
        "📣 <b>Розсилка</b>\n\nНадішліть повідомлення (текст або фото з підписом).",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(BroadcastFSM.message)


async def broadcast_got_message(message: types.Message, state: FSMContext) -> None:
    if message.photo:
        await state.update_data(broadcast_photo=message.photo[-1].file_id, broadcast_text=message.caption or "")
    elif message.text:
        await state.update_data(broadcast_photo=None, broadcast_text=message.text)
    else:
        await message.answer("❌ Підтримуються лише текст або фото з підписом.")
        return

    data = await state.get_data()
    preview = data["broadcast_text"][:300] or "(фото без підпису)"
    await message.answer(
        f"📋 <b>Попередній перегляд:</b>\n\n{preview}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="✅ Відправити всім", callback_data="tt_adm_bc:confirm"),
            types.InlineKeyboardButton(text="❌ Скасувати",       callback_data="tt_adm_cancel"),
        ]]),
    )
    await state.set_state(BroadcastFSM.confirm)


async def broadcast_confirm(
    callback: types.CallbackQuery, state: FSMContext, session: AsyncSession,
    registered_bot_id: int, bot: Bot,
) -> None:
    data = await state.get_data()
    photo, text = data.get("broadcast_photo"), data.get("broadcast_text", "")
    await state.clear()

    result = await session.execute(
        select(BotSubscription.telegram_id).where(BotSubscription.bot_id == registered_bot_id)
    )
    recipients = [row[0] for row in result.all()]

    sent = failed = 0
    for tid in recipients:
        try:
            if photo:
                await bot.send_photo(chat_id=tid, photo=photo, caption=text or None)
            else:
                await bot.send_message(chat_id=tid, text=text)
            sent += 1
        except Exception:
            failed += 1

    await _safe_edit(
        callback.message,
        f"✅ Розсилку завершено!\n\n📤 Надіслано: {sent}\n❌ Помилок: {failed}",
        reply_markup=_back_menu_kb(),
    )
    await callback.answer()


# ── Shared cancel ─────────────────────────────────────────────────────────────

async def admin_fsm_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(callback.message, "❌ Скасовано.", reply_markup=_back_menu_kb())
    await callback.answer()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cancel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="❌ Скасувати", callback_data="tt_adm_cancel")
    ]])


async def _marked_days(session: AsyncSession, bot_id: int, year: int, month: int) -> set[int]:
    result = await session.execute(
        select(TattooBooking.date).where(
            TattooBooking.bot_id == bot_id,
            TattooBooking.status == BookingStatus.NEW,
        )
    )
    marked: set[int] = set()
    for (d_str,) in result.all():
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
            if d.year == year and d.month == month:
                marked.add(d.day)
        except ValueError:
            pass
    return marked


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_menu_admin, Command("menu"))
    dp.message.register(cmd_menu_admin, Command("back"))

    # Main menu
    dp.callback_query.register(admin_menu_callback, F.data.startswith("tt_adm:"))

    # Settings
    dp.callback_query.register(settings_callback,   F.data.startswith("tt_cfg:"))
    dp.message.register(settings_got_social,         SettingsFSM.social)
    dp.message.register(settings_got_welcome,        SettingsFSM.welcome)
    dp.message.register(settings_got_slots,          SettingsFSM.slots)

    # Categories
    dp.callback_query.register(category_add_start, F.data == "tt_cat_add")
    dp.callback_query.register(category_delete,    F.data.startswith("tt_cat_del:"))
    dp.message.register(category_got_name,         CategoryFSM.add_name)

    # Add portfolio
    dp.message.register(portfolio_got_photo,         AddPortfolioFSM.photo, F.photo)
    dp.callback_query.register(portfolio_got_style,  F.data.startswith("tt_adm_style:"), AddPortfolioFSM.style)
    dp.message.register(portfolio_got_description,   AddPortfolioFSM.description)
    dp.message.register(portfolio_got_work_time,     AddPortfolioFSM.work_time)
    dp.message.register(portfolio_got_price,         AddPortfolioFSM.price)

    # Browse & delete portfolio
    dp.callback_query.register(admin_portfolio_browse,  F.data.startswith("tt_adm_plist:"))
    dp.callback_query.register(admin_portfolio_delete,  F.data.startswith("tt_adm_pdel:"))

    # Schedule
    dp.callback_query.register(schedule_nav,                F.data.startswith("tt_adm_nav:"))
    dp.callback_query.register(schedule_day,                F.data.startswith("tt_adm_day:"))
    dp.callback_query.register(admin_cancel_booking_start,  F.data.startswith("tt_adm_cancel_book:"))
    dp.message.register(admin_cancel_booking_reason,        CancelBookingFSM.reason)

    # Review moderation
    dp.callback_query.register(review_approve, F.data.startswith("tt_ra_approve:"))
    dp.callback_query.register(review_delete,  F.data.startswith("tt_ra_delete:"))

    # Broadcast
    dp.message.register(broadcast_got_message,   BroadcastFSM.message)
    dp.callback_query.register(broadcast_confirm, F.data == "tt_adm_bc:confirm", BroadcastFSM.confirm)

    # Cancel
    dp.callback_query.register(admin_fsm_cancel, F.data == "tt_adm_cancel")
