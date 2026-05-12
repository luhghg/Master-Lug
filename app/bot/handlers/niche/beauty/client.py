"""Client-facing handlers for the Beauty (tattoo) niche."""
import logging
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tattoo import (
    BookingStatus, ReviewStatus,
    TattooBooking, TattooPortfolio, TattooReview,
)
from app.bot.handlers.niche.beauty.config import (
    MAX_TEXT_LEN, SOCIAL_TEXT as DEFAULT_SOCIAL,
    TIME_SLOTS as DEFAULT_SLOTS, DEFAULT_CATEGORIES,
)
from app.bot.handlers.niche.beauty.calendar_widget import make_calendar
from app.services.config_service import (
    CATEGORIES, SOCIAL_TEXT, TIME_SLOTS, WELCOME_TEXT, get_cfg, get_json, is_demo_bot,
)

logger = logging.getLogger(__name__)


class BookingFSM(StatesGroup):
    idea      = State()
    body_part = State()
    size      = State()
    pick_date = State()
    pick_time = State()
    contact   = State()


class ReviewFSM(StatesGroup):
    text  = State()
    photo = State()


# ── Dynamic config ────────────────────────────────────────────────────────────

async def _categories(session: AsyncSession, bot_id: int) -> list[dict]:
    return await get_json(session, bot_id, CATEGORIES, DEFAULT_CATEGORIES)

async def _slots(session: AsyncSession, bot_id: int) -> list[str]:
    return await get_json(session, bot_id, TIME_SLOTS, DEFAULT_SLOTS)

async def _social(session: AsyncSession, bot_id: int) -> str:
    return await get_cfg(session, bot_id, SOCIAL_TEXT, DEFAULT_SOCIAL)

async def _welcome(session: AsyncSession, bot_id: int) -> str:
    return await get_cfg(session, bot_id, WELCOME_TEXT, "👋 <b>Ласкаво просимо!</b>\n\nОберіть що вас цікавить:")


# ── UI helpers ────────────────────────────────────────────────────────────────

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    """Edit message in place; fall back to new message if editing fails (e.g. photo messages)."""
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


def _home_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🏠 Меню", callback_data="tt_menu:home")
    ]])


def _menu_markup() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎨 Портфоліо",  callback_data="tt_menu:portfolio"),
         types.InlineKeyboardButton(text="📅 Записатись", callback_data="tt_menu:booking")],
        [types.InlineKeyboardButton(text="⭐️ Відгуки",   callback_data="tt_menu:reviews"),
         types.InlineKeyboardButton(text="📱 Соцмережі",  callback_data="tt_menu:social")],
    ])


# ── Client menu ───────────────────────────────────────────────────────────────

async def show_client_menu(message: types.Message, session: AsyncSession = None, registered_bot_id: int = 0) -> None:
    text = "👋 <b>Ласкаво просимо!</b>\n\nОберіть що вас цікавить:"
    if session and registered_bot_id:
        text = await _welcome(session, registered_bot_id)
    await message.answer(text, reply_markup=_menu_markup())


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
    msg = callback.message

    if action == "home":
        text = "👋 <b>Ласкаво просимо!</b>\n\nОберіть що вас цікавить:"
        if session and registered_bot_id:
            text = await _welcome(session, registered_bot_id)
        await _safe_edit(msg, text, reply_markup=_menu_markup())

    elif action == "portfolio":
        await _show_category_selector(msg, session, registered_bot_id)

    elif action == "booking":
        await state.clear()
        await _safe_edit(
            msg,
            "📅 <b>Запис до майстра</b>\n\nКрок 1/6 — Опишіть ідею татуювання:",
            reply_markup=_cancel_kb(),
        )
        await state.set_state(BookingFSM.idea)

    elif action == "reviews":
        await _reviews_show(msg, session, registered_bot_id, 0)

    elif action == "social":
        social_text = await _social(session, registered_bot_id)
        await _safe_edit(msg, social_text, reply_markup=_home_kb())


# ── Portfolio ─────────────────────────────────────────────────────────────────

async def _show_category_selector(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    cats = await _categories(session, bot_id)
    if not cats:
        await _safe_edit(
            message,
            "😔 Категорій ще немає. Зверніться до адміна.",
            reply_markup=_home_kb(),
        )
        return
    rows = [[types.InlineKeyboardButton(text=c["name"], callback_data=f"tt_p_style:{c['key']}")] for c in cats]
    rows.append([types.InlineKeyboardButton(text="🏠 Меню", callback_data="tt_menu:home")])
    await _safe_edit(
        message,
        "🎨 <b>Оберіть категорію:</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def portfolio_style(callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int) -> None:
    style_key = callback.data.split(":")[1]
    await _show_portfolio_page(callback.message, session, registered_bot_id, style_key, 0, edit=False)
    await callback.answer()


async def portfolio_navigate(callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int) -> None:
    _, style_key, idx_str = callback.data.split(":")
    await _show_portfolio_page(callback.message, session, registered_bot_id, style_key, int(idx_str), edit=True)
    await callback.answer()


async def _show_portfolio_page(
    message: types.Message, session: AsyncSession, bot_id: int,
    style_key: str, idx: int, edit: bool = False,
) -> None:
    cats = await _categories(session, bot_id)
    cat_name = next((c["name"] for c in cats if c["key"] == style_key), style_key)

    result = await session.execute(
        select(TattooPortfolio)
        .where(TattooPortfolio.bot_id == bot_id, TattooPortfolio.style == style_key)
        .order_by(TattooPortfolio.created_at)
    )
    works = list(result.scalars().all())
    if not works:
        await message.answer(f"😔 У категорії <b>{cat_name}</b> поки немає робіт.")
        return

    idx = max(0, min(idx, len(works) - 1))
    work = works[idx]
    work.view_count = (work.view_count or 0) + 1
    await session.commit()
    caption = (
        f"🎨 <b>{cat_name}</b>  [{idx + 1}/{len(works)}]\n\n"
        f"📝 {work.description}\n⏱ Час: {work.work_time}\n💰 Ціна: {work.price}"
    )
    nav = []
    if idx > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"tt_p_view:{style_key}:{idx - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{idx + 1}/{len(works)}", callback_data="tt_ignore"))
    if idx < len(works) - 1:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"tt_p_view:{style_key}:{idx + 1}"))

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text="🔥 Хочу так само!", callback_data=f"tt_p_want:{work.id}")],
        [types.InlineKeyboardButton(text="◀️ Категорії", callback_data="tt_p_back_styles")],
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


async def portfolio_back_styles(callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _show_category_selector(callback.message, session, registered_bot_id)
    await callback.answer()


async def portfolio_want(
    callback: types.CallbackQuery, state: FSMContext, session: AsyncSession,
    bot: Bot, owner_telegram_id: int,
) -> None:
    work_id = int(callback.data.split(":")[1])
    work = await session.get(TattooPortfolio, work_id)
    cats = await _categories(session, work.bot_id)
    cat_name = next((c["name"] for c in cats if c["key"] == work.style), work.style)

    user = callback.from_user
    mention = f"@{user.username}" if user.username else user.full_name
    demo = is_demo_bot(work.bot_id)
    notify_id = user.id if demo else owner_telegram_id
    prefix = "📬 <b>Так виглядає повідомлення майстру:</b>\n\n" if demo else ""
    try:
        await bot.send_message(
            chat_id=notify_id,
            text=(
                f"{prefix}"
                f"🔥 <b>Клієнт зацікавився роботою!</b>\n\n"
                f"👤 {mention} (ID: {user.id})\n"
                f"🎨 {cat_name}\n📝 {work.description}\n💰 {work.price}"
            ),
        )
    except Exception:
        pass

    await state.update_data(reference_id=work_id)
    await callback.message.answer("🔥 Чудовий вибір! Оформимо запис.\n\n📝 Опишіть вашу ідею:")
    await state.set_state(BookingFSM.idea)
    await callback.answer()


# ── Booking FSM ───────────────────────────────────────────────────────────────

async def booking_idea(message: types.Message, state: FSMContext) -> None:
    if not _valid_text(message.text):
        await message.answer(f"❌ Введіть від 2 до {MAX_TEXT_LEN} символів.")
        return
    await state.update_data(idea=message.text.strip())
    await message.answer("Крок 2/6 — Місце на тілі (передпліччя, лопатка...):", reply_markup=_cancel_kb())
    await state.set_state(BookingFSM.body_part)


async def booking_body_part(message: types.Message, state: FSMContext) -> None:
    if not _valid_text(message.text):
        await message.answer(f"❌ Введіть від 2 до {MAX_TEXT_LEN} символів.")
        return
    await state.update_data(body_part=message.text.strip())
    await message.answer("Крок 3/6 — Бажаний розмір (10×15 см, маленьке ~5 см...):", reply_markup=_cancel_kb())
    await state.set_state(BookingFSM.size)


async def booking_size(message: types.Message, state: FSMContext) -> None:
    if not _valid_text(message.text):
        await message.answer(f"❌ Введіть від 2 до {MAX_TEXT_LEN} символів.")
        return
    await state.update_data(size=message.text.strip())
    today = date.today()
    await message.answer(
        "Крок 4/6 — Оберіть дату:",
        reply_markup=make_calendar(today.year, today.month, "tt_b_nav", "tt_b_day"),
    )
    await state.set_state(BookingFSM.pick_date)


async def booking_cal_nav(callback: types.CallbackQuery) -> None:
    _, year_s, month_s = callback.data.split(":")
    await callback.message.edit_reply_markup(
        reply_markup=make_calendar(int(year_s), int(month_s), "tt_b_nav", "tt_b_day")
    )
    await callback.answer()


async def booking_day_selected(
    callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    date_str = callback.data.split(":")[1]
    if datetime.strptime(date_str, "%Y-%m-%d").date() < date.today():
        await callback.answer("❌ Ця дата вже минула!", show_alert=True)
        return
    await state.update_data(booking_date=date_str)
    booked = await _booked_slots(session, registered_bot_id, date_str)
    available = await _slots(session, registered_bot_id)
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    await callback.message.answer(
        f"Крок 5/6 — Оберіть час на <b>{d.strftime('%d.%m.%Y')}</b>:",
        reply_markup=_time_slots_kb(available, booked),
    )
    await state.set_state(BookingFSM.pick_time)
    await callback.answer()


async def booking_slot_selected(
    callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    time_slot = callback.data.split(":")[1]
    data = await state.get_data()
    booked = await _booked_slots(session, registered_bot_id, data["booking_date"])
    if time_slot in booked:
        available = await _slots(session, registered_bot_id)
        await callback.message.edit_text(
            "⚠️ Цей час щойно зайняли! Оберіть інший:",
            reply_markup=_time_slots_kb(available, booked),
        )
        await callback.answer()
        return
    await state.update_data(time_slot=time_slot)
    await callback.message.answer(
        "Крок 6/6 — Поділіться номером телефону:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="📱 Поділитись контактом", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True,
        ),
    )
    await state.set_state(BookingFSM.contact)
    await callback.answer()


async def booking_back_to_cal(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    d = datetime.strptime(data["booking_date"], "%Y-%m-%d").date() if data.get("booking_date") else date.today()
    await callback.message.answer(
        "Крок 4/6 — Оберіть дату:",
        reply_markup=make_calendar(d.year, d.month, "tt_b_nav", "tt_b_day"),
    )
    await state.set_state(BookingFSM.pick_date)
    await callback.answer()


async def booking_got_contact(
    message: types.Message, state: FSMContext, session: AsyncSession,
    registered_bot_id: int, bot: Bot, owner_telegram_id: int,
) -> None:
    phone = message.contact.phone_number
    data = await state.get_data()
    booking = TattooBooking(
        bot_id=registered_bot_id, user_id=message.from_user.id,
        idea=data["idea"], body_part=data["body_part"], size=data["size"],
        date=data["booking_date"], time_slot=data["time_slot"],
        phone=phone, reference_id=data.get("reference_id"), status=BookingStatus.NEW,
    )
    session.add(booking)
    await session.commit()
    await state.clear()

    d = datetime.strptime(data["booking_date"], "%Y-%m-%d").date()
    await message.answer(
        f"✅ <b>Запис підтверджено!</b>\n\n📅 {d.strftime('%d.%m.%Y')} о {data['time_slot']}\n\nМайстер зв'яжеться з вами. Дякуємо! 🙏",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await message.answer("Чим іще можу допомогти?", reply_markup=_home_kb())

    user = message.from_user
    mention = f"@{user.username}" if user.username else user.full_name
    ref_text = ""
    if data.get("reference_id"):
        work = await session.get(TattooPortfolio, data["reference_id"])
        if work:
            cats = await _categories(session, registered_bot_id)
            cat_name = next((c["name"] for c in cats if c["key"] == work.style), work.style)
            ref_text = f"\n🖼 Референс: {cat_name} — {work.description}"
    demo = is_demo_bot(registered_bot_id)
    notify_id = user.id if demo else owner_telegram_id
    prefix = "📬 <b>Так виглядає повідомлення майстру:</b>\n\n" if demo else ""
    try:
        await bot.send_message(
            chat_id=notify_id,
            text=(
                f"{prefix}"
                f"📅 <b>Новий запис!</b>\n\n👤 {mention} ({phone})\n"
                f"📅 {d.strftime('%d.%m.%Y')} о {data['time_slot']}\n"
                f"💡 {data['idea']}\n📍 {data['body_part']} | 📐 {data['size']}{ref_text}"
            ),
        )
    except Exception:
        logger.warning("Could not notify owner about new booking")


async def booking_contact_invalid(message: types.Message) -> None:
    await message.answer(
        "❌ Натисніть кнопку <b>«📱 Поділитись контактом»</b>.",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="📱 Поділитись контактом", request_contact=True)]],
            resize_keyboard=True,
        ),
    )


async def booking_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    await state.clear()
    if current_state == BookingFSM.contact.state:
        await callback.message.answer("❌ Запис скасовано.", reply_markup=types.ReplyKeyboardRemove())
        await callback.message.answer("Повертайтесь коли будете готові 😊", reply_markup=_home_kb())
    else:
        await _safe_edit(
            callback.message,
            "❌ Запис скасовано. Повертайтесь коли будете готові 😊",
            reply_markup=_home_kb(),
        )
    await callback.answer()


# ── Reviews ───────────────────────────────────────────────────────────────────

async def reviews_page(callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int) -> None:
    await _reviews_show(callback.message, session, registered_bot_id, int(callback.data.split(":")[1]))
    await callback.answer()


async def _reviews_show(message: types.Message, session: AsyncSession, bot_id: int, page: int) -> None:
    result = await session.execute(
        select(TattooReview)
        .where(TattooReview.bot_id == bot_id, TattooReview.status == ReviewStatus.APPROVED)
        .order_by(TattooReview.created_at.desc())
    )
    reviews = list(result.scalars().all())
    if not reviews:
        await _safe_edit(
            message,
            "😔 Відгуків ще немає. Будьте першим!",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✍️ Залишити відгук", callback_data="tt_review:add")],
                [types.InlineKeyboardButton(text="🏠 Меню", callback_data="tt_menu:home")],
            ]),
        )
        return

    page = max(0, min(page, len(reviews) - 1))
    r = reviews[page]
    text = f"⭐️ <b>{r.user_name or 'Анонім'}</b>\n🗓 {r.created_at.strftime('%d.%m.%Y')}\n\n{r.text}"
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"tt_r_page:{page - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{len(reviews)}", callback_data="tt_ignore"))
    if page < len(reviews) - 1:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"tt_r_page:{page + 1}"))

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text="✍️ Залишити відгук", callback_data="tt_review:add")],
        [types.InlineKeyboardButton(text="🏠 Меню", callback_data="tt_menu:home")],
    ])
    if r.photo_id:
        await message.answer_photo(photo=r.photo_id, caption=text, reply_markup=kb)
    else:
        await _safe_edit(message, text, reply_markup=kb)


async def review_add_start(
    callback: types.CallbackQuery, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    existing = await session.execute(
        select(TattooReview).where(
            TattooReview.bot_id == registered_bot_id,
            TattooReview.user_id == callback.from_user.id,
            TattooReview.status == ReviewStatus.APPROVED,
        )
    )
    if existing.scalar_one_or_none():
        await callback.answer("Ви вже залишили відгук 😊", show_alert=True)
        return

    await _safe_edit(callback.message, "✍️ Напишіть ваш відгук:", reply_markup=_cancel_kb())
    await state.set_state(ReviewFSM.text)
    await callback.answer()


async def review_got_text(message: types.Message, state: FSMContext) -> None:
    if not _valid_text(message.text):
        await message.answer(f"❌ Введіть від 2 до {MAX_TEXT_LEN} символів.")
        return
    await state.update_data(review_text=message.text.strip())
    await message.answer(
        "📸 Прикріпіть фото роботи (або пропустіть):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="➡️ Без фото", callback_data="tt_review:skip_photo")
        ]]),
    )
    await state.set_state(ReviewFSM.photo)


async def review_got_photo(
    message: types.Message, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    photo_id = message.photo[-1].file_id if message.photo else None
    await _save_review(message, state, session, registered_bot_id, photo_id)


async def review_skip_photo(
    callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, registered_bot_id: int,
) -> None:
    await _save_review(callback.message, state, session, registered_bot_id, None)
    await callback.answer()


async def _save_review(
    message: types.Message, state: FSMContext, session: AsyncSession, bot_id: int, photo_id: str | None,
) -> None:
    data = await state.get_data()
    user = message.from_user
    review = TattooReview(
        bot_id=bot_id,
        user_id=user.id if user else 0,
        user_name=user.full_name if user else None,
        text=data["review_text"],
        photo_id=photo_id,
        status=ReviewStatus.APPROVED,
    )
    session.add(review)
    await session.commit()
    await state.clear()
    await _safe_edit(message, "✅ Дякуємо за відгук! Він вже опубліковано 🙏", reply_markup=_home_kb())


async def review_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(callback.message, "❌ Скасовано.", reply_markup=_home_kb())
    await callback.answer()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_text(text: str | None) -> bool:
    return bool(text and 2 <= len(text.strip()) <= MAX_TEXT_LEN)


def _cancel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="❌ Скасувати", callback_data="tt_booking_cancel")
    ]])


async def _booked_slots(session: AsyncSession, bot_id: int, date_str: str) -> set[str]:
    result = await session.execute(
        select(TattooBooking.time_slot).where(
            TattooBooking.bot_id == bot_id,
            TattooBooking.date == date_str,
            TattooBooking.status == BookingStatus.NEW,
        )
    )
    return {row[0] for row in result.all()}


def _time_slots_kb(available: list[str], booked: set[str]) -> types.InlineKeyboardMarkup:
    rows, row = [], []
    for slot in available:
        if slot in booked:
            row.append(types.InlineKeyboardButton(text=f"🚫 {slot}", callback_data="tt_ignore"))
        else:
            row.append(types.InlineKeyboardButton(text=f"🕐 {slot}", callback_data=f"tt_b_slot:{slot}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([types.InlineKeyboardButton(text="◀️ Назад до календаря", callback_data="tt_b_back_cal")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_menu, Command("menu"))
    dp.message.register(cmd_menu, Command("back"))
    dp.callback_query.register(menu_callback,         F.data.startswith("tt_menu:"))
    dp.callback_query.register(portfolio_style,       F.data.startswith("tt_p_style:"))
    dp.callback_query.register(portfolio_navigate,    F.data.startswith("tt_p_view:"))
    dp.callback_query.register(portfolio_back_styles, F.data == "tt_p_back_styles")
    dp.callback_query.register(portfolio_want,        F.data.startswith("tt_p_want:"))
    dp.message.register(booking_idea,                 BookingFSM.idea)
    dp.message.register(booking_body_part,            BookingFSM.body_part)
    dp.message.register(booking_size,                 BookingFSM.size)
    dp.callback_query.register(booking_cal_nav,       F.data.startswith("tt_b_nav:"), BookingFSM.pick_date)
    dp.callback_query.register(booking_day_selected,  F.data.startswith("tt_b_day:"), BookingFSM.pick_date)
    dp.callback_query.register(booking_slot_selected, F.data.startswith("tt_b_slot:"), BookingFSM.pick_time)
    dp.callback_query.register(booking_back_to_cal,   F.data == "tt_b_back_cal",       BookingFSM.pick_time)
    dp.message.register(booking_got_contact,          BookingFSM.contact, F.contact)
    dp.message.register(booking_contact_invalid,      BookingFSM.contact)
    dp.callback_query.register(booking_cancel,        F.data == "tt_booking_cancel")
    dp.callback_query.register(reviews_page,          F.data.startswith("tt_r_page:"))
    dp.callback_query.register(review_add_start,      F.data == "tt_review:add")
    dp.message.register(review_got_text,              ReviewFSM.text)
    dp.message.register(review_got_photo,             ReviewFSM.photo, F.photo)
    dp.callback_query.register(review_skip_photo,     F.data == "tt_review:skip_photo", ReviewFSM.photo)
    dp.callback_query.register(review_cancel,         F.data == "tt_booking_cancel",    ReviewFSM.text)
    dp.callback_query.register(lambda c: c.answer(),  F.data == "tt_ignore")
