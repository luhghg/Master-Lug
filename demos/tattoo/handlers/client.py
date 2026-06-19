from pathlib import Path

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InputMediaPhoto

from db import save_booking, get_booking
from demo_data import (
    MASTER, PORTFOLIO, STYLES, DEMO_SLOTS,
    REMINDER_PREVIEW, AFTERCARE_PREVIEW, REVIEW_PREVIEW,
)


class BookingFSM(StatesGroup):
    style = State()
    zone = State()
    reference = State()
    allergy = State()
    slot = State()
    awaiting_payment = State()


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _client_menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="🖼 Портфоліо", callback_data="c:portfolio:0"),
            types.InlineKeyboardButton(text="📅 Записатись", callback_data="c:book"),
        ],
        [
            types.InlineKeyboardButton(text="💰 Ціни", callback_data="c:prices"),
            types.InlineKeyboardButton(text="📞 Контакти", callback_data="c:contacts"),
        ],
    ])


def _portfolio_kb(idx: int) -> types.InlineKeyboardMarkup:
    total = len(PORTFOLIO)
    nav = []
    if idx > 0:
        nav.append(types.InlineKeyboardButton(text="◀️", callback_data=f"c:portfolio:{idx - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{idx + 1}/{total}", callback_data="noop"))
    if idx < total - 1:
        nav.append(types.InlineKeyboardButton(text="▶️", callback_data=f"c:portfolio:{idx + 1}"))
    return types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text="📅 Записатись на цей стиль", callback_data=f"c:book_style:{idx}")],
        [types.InlineKeyboardButton(text="◀️ Меню", callback_data="c:menu")],
    ])


def _styles_kb() -> types.InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(STYLES), 2):
        row = [types.InlineKeyboardButton(text=STYLES[i], callback_data=f"c:style:{STYLES[i]}")]
        if i + 1 < len(STYLES):
            row.append(types.InlineKeyboardButton(text=STYLES[i + 1], callback_data=f"c:style:{STYLES[i + 1]}"))
        rows.append(row)
    rows.append([types.InlineKeyboardButton(text="🖼 Не знаю, покажіть приклади", callback_data="c:portfolio:0")])
    rows.append([types.InlineKeyboardButton(text="❌ Скасувати", callback_data="c:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _zone_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="c:back:zone")],
        [types.InlineKeyboardButton(text="❌ Скасувати", callback_data="c:cancel")],
    ])


def _reference_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="c:back:reference")],
        [types.InlineKeyboardButton(text="❌ Скасувати", callback_data="c:cancel")],
    ])


def _allergy_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Ні, все добре", callback_data="c:allergy:no"),
            types.InlineKeyboardButton(text="⚠️ Є алергія", callback_data="c:allergy:yes"),
        ],
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="c:back:allergy")],
        [types.InlineKeyboardButton(text="❌ Скасувати", callback_data="c:cancel")],
    ])


def _slots_kb() -> types.InlineKeyboardMarkup:
    rows = [
        [types.InlineKeyboardButton(text=slot, callback_data=f"c:slot:{i}")]
        for i, slot in enumerate(DEMO_SLOTS)
    ]
    rows.append([types.InlineKeyboardButton(text="❌ Скасувати", callback_data="c:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _payment_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Я оплатив(ла)", callback_data="c:paid")],
        [types.InlineKeyboardButton(text="❌ Скасувати", callback_data="c:cancel")],
    ])


def _after_booking_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⏰ Як виглядає нагадування", callback_data="c:preview:reminder")],
        [types.InlineKeyboardButton(text="🌿 Догляд після сеансу", callback_data="c:preview:aftercare")],
        [types.InlineKeyboardButton(text="⭐ Запит відгуку", callback_data="c:preview:review")],
        [types.InlineKeyboardButton(text="◀️ Меню", callback_data="c:menu")],
    ])


def _preview_back_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="c:after_booking")],
    ])


def _back_menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Меню", callback_data="c:menu")],
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


# ── Main menu ─────────────────────────────────────────────────────────────────

async def show_client_menu(message: types.Message) -> None:
    await message.answer(
        f"🖤 <b>Привіт! Я {MASTER['full_name']}</b>\n\n"
        f"{MASTER['bio']}\n\n"
        f"Спеціалізація: <b>{MASTER['specialization']}</b>",
        reply_markup=_client_menu_kb(),
    )


async def client_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(
        callback.message,
        f"🖤 <b>{MASTER['full_name']}</b>\n\n"
        f"{MASTER['bio']}\n\n"
        f"Спеціалізація: <b>{MASTER['specialization']}</b>",
        reply_markup=_client_menu_kb(),
    )
    await callback.answer()


# ── Portfolio ─────────────────────────────────────────────────────────────────

async def show_portfolio(callback: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":")[2])
    item = PORTFOLIO[idx]
    caption = (
        f"{item['emoji']} <b>{item['style']} — {item['title']}</b>\n\n"
        f"📝 {item['description']}\n\n"
        f"⏱ Час роботи: <b>{item['hours']}</b>\n"
        f"💰 Ціна: <b>{item['price']}</b>"
    )
    kb = _portfolio_kb(idx)

    photo_path = item.get("photo_path")
    if photo_path and Path(photo_path).exists():
        if callback.message.photo:
            # Already a photo message — replace media in-place, no new message.
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=FSInputFile(photo_path),
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=kb,
            )
        else:
            # First photo in this navigation — send new photo message.
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer_photo(
                photo=FSInputFile(photo_path),
                caption=caption,
                reply_markup=kb,
            )
    else:
        text = (
            f"{item['emoji']} <b>{item['style']} — {item['title']}</b>\n\n"
            f"📸 <i>[PHOTO PLACEHOLDER: {item['style']} — {item['title']}]</i>\n"
            f"<i>Тут буде реальне фото роботи</i>\n\n"
            f"📝 {item['description']}\n\n"
            f"⏱ Час роботи: <b>{item['hours']}</b>\n"
            f"💰 Ціна: <b>{item['price']}</b>"
        )
        if callback.message.photo:
            # Switching from photo back to placeholder — send new text message.
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=kb)
        else:
            await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


# ── Prices & contacts ─────────────────────────────────────────────────────────

async def show_prices(callback: types.CallbackQuery) -> None:
    text = (
        "💰 <b>Ціни та умови</b>\n\n"
        f"⏱ <b>{MASTER['price_from']} грн/год</b> — базова ставка\n\n"
        "Орієнтовно:\n"
        "• Маленьке тату (до 5 см) — від 1 500 грн\n"
        "• Середнє (5–15 см) — від 3 000 грн\n"
        "• Велике (15+ см) — від 6 000 грн\n"
        "• Рукав / великий проект — від 15 000 грн\n\n"
        "💳 <b>Депозит:</b> 500 грн при записі\n"
        "<i>(Входить у вартість сеансу)</i>\n\n"
        "📋 Точна ціна — після обговорення ескізу.\n"
        "Консультація безкоштовна."
    )
    await _safe_edit(callback.message, text, reply_markup=_back_menu_kb())
    await callback.answer()


async def show_contacts(callback: types.CallbackQuery) -> None:
    text = (
        f"📞 <b>Контакти</b>\n\n"
        f"📍 {MASTER['address']}\n"
        f"📸 Instagram: {MASTER['instagram']}\n\n"
        "💬 Написати напряму: просто надішліть повідомлення у цей бот\n\n"
        "<i>Або скористайтесь кнопкою «Записатись» — це швидше.</i>"
    )
    await _safe_edit(callback.message, text, reply_markup=_back_menu_kb())
    await callback.answer()


# ── Booking flow ──────────────────────────────────────────────────────────────

async def start_booking(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BookingFSM.style)
    await _safe_edit(
        callback.message,
        "📋 <b>Запис на сеанс</b>\n\n"
        "<b>Крок 1 з 4</b> — Який стиль татуювання вас цікавить?",
        reply_markup=_styles_kb(),
    )
    await callback.answer()


async def start_booking_with_style(callback: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":")[2])
    style = PORTFOLIO[idx]["style"]
    await state.clear()
    await state.update_data(style=style)
    await state.set_state(BookingFSM.zone)
    await _safe_edit(
        callback.message,
        f"📋 <b>Запис на сеанс</b>\n\n"
        f"✅ Стиль: <b>{style}</b>\n\n"
        f"<b>Крок 2 з 4</b> — Де буде тату і приблизний розмір?\n\n"
        f"<i>Наприклад: «Ліве передпліччя, 15×10 см»</i>",
        reply_markup=_zone_kb(),
    )
    await callback.answer()


async def got_style(callback: types.CallbackQuery, state: FSMContext) -> None:
    style = callback.data.split(":", 2)[2]
    await state.update_data(style=style)
    await state.set_state(BookingFSM.zone)
    await _safe_edit(
        callback.message,
        f"📋 <b>Запис на сеанс</b>\n\n"
        f"✅ Стиль: <b>{style}</b>\n\n"
        f"<b>Крок 2 з 4</b> — Де буде тату і приблизний розмір?\n\n"
        f"<i>Наприклад: «Ліве передпліччя, 15×10 см»</i>",
        reply_markup=_zone_kb(),
    )
    await callback.answer()


async def got_zone(message: types.Message, state: FSMContext) -> None:
    await state.update_data(zone=message.text)
    await state.set_state(BookingFSM.reference)
    data = await state.get_data()
    await message.answer(
        f"📋 <b>Запис на сеанс</b>\n\n"
        f"✅ Стиль: <b>{data['style']}</b>\n"
        f"✅ Зона: <b>{data['zone']}</b>\n\n"
        f"<b>Крок 3 з 4</b> — Надішліть референс або опишіть ідею.\n\n"
        f"<i>Можна прикріпити фото, посилання або просто текст.\n"
        f"Якщо поки немає ідеї — напишіть «поки не знаю»</i>",
        reply_markup=_reference_kb(),
    )


async def got_reference_text(message: types.Message, state: FSMContext) -> None:
    await state.update_data(reference=message.text)
    await _ask_allergy(message, state)


async def got_reference_photo(message: types.Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(
        reference="[Фото-референс надіслано ✅]",
        reference_file_id=file_id,
    )
    await _ask_allergy(message, state)


async def _ask_allergy(message: types.Message, state: FSMContext) -> None:
    await state.set_state(BookingFSM.allergy)
    data = await state.get_data()
    await message.answer(
        f"📋 <b>Запис на сеанс</b>\n\n"
        f"✅ Стиль: <b>{data['style']}</b>\n"
        f"✅ Зона: <b>{data['zone']}</b>\n"
        f"✅ Референс: <b>{data['reference']}</b>\n\n"
        f"<b>Крок 4 з 4</b> — Чи є алергія на фарбники або латекс?",
        reply_markup=_allergy_kb(),
    )


async def got_allergy(callback: types.CallbackQuery, state: FSMContext) -> None:
    allergy_value = callback.data.split(":")[2]
    allergy_text = "Немає" if allergy_value == "no" else "⚠️ Є — уточнимо перед сеансом"
    await state.update_data(allergy=allergy_text)
    await state.set_state(BookingFSM.slot)
    await callback.message.edit_text(
        "📋 <b>Запис на сеанс</b>\n\n"
        "Майже готово! Оберіть зручний час 👇",
        reply_markup=_slots_kb(),
    )
    await callback.answer()


async def got_slot(callback: types.CallbackQuery, state: FSMContext) -> None:
    idx = int(callback.data.split(":")[2])
    slot = DEMO_SLOTS[idx]
    await state.update_data(slot=slot)
    data = await state.get_data()

    await state.set_state(BookingFSM.awaiting_payment)
    await callback.message.edit_text(
        f"📋 <b>Ваш запис</b>\n\n"
        f"🎨 Стиль: <b>{data['style']}</b>\n"
        f"📍 Зона: <b>{data['zone']}</b>\n"
        f"🖼 Референс: <b>{data['reference']}</b>\n"
        f"⚕️ Алергія: <b>{data['allergy']}</b>\n"
        f"📅 Час: <b>{slot}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Депозит: {MASTER['deposit']} грн</b>\n\n"
        f"<i>📌 Реквізити майстра будуть тут\n"
        f"(Наприклад: картка Monobank, ПриватБанк)\n\n"
        f"Призначення платежу: «Тату Оля депозит»</i>\n\n"
        f"Після оплати натисніть кнопку нижче:",
        reply_markup=_payment_kb(),
    )
    await callback.answer()


async def got_payment(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await save_booking(callback.from_user.id, data)
    await state.clear()

    await callback.message.edit_text(
        f"🎉 <b>Запис підтверджено!</b>\n\n"
        f"📅 <b>{data.get('slot', '—')}</b>\n"
        f"📍 {MASTER['address']}\n\n"
        f"<b>Що взяти з собою:</b>\n"
        f"• Поїж і випий воду перед сеансом\n"
        f"• Одяг так, щоб зона для тату була вільна\n"
        f"• Гарний настрій 😊\n\n"
        f"<i>Нагадування прийде за 24 год і за 2 год до сеансу.</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👇 Подивіться як виглядають автоматичні повідомлення:",
        reply_markup=_after_booking_kb(),
    )
    await callback.answer("✅ Запис підтверджено!")


async def show_after_booking(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(
        "🎉 <b>Запис підтверджено!</b>\n\n"
        "👇 Подивіться як виглядають автоматичні повідомлення:",
        reply_markup=_after_booking_kb(),
    )
    await callback.answer()


# ── Preview screens ───────────────────────────────────────────────────────────

async def preview_reminder(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(
        "💡 <i>Ось так виглядає автонагадування за 24 год до сеансу:</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        + REMINDER_PREVIEW +
        "\n\n━━━━━━━━━━━━━━━━━━\n"
        "<i>Надсилається автоматично — майстер нічого не робить вручну.</i>",
        reply_markup=_preview_back_kb(),
    )
    await callback.answer()


async def preview_aftercare(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(
        "💡 <i>Ось так виглядає повідомлення з доглядом після сеансу:</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        + AFTERCARE_PREVIEW +
        "\n\n━━━━━━━━━━━━━━━━━━\n"
        "<i>Надсилається автоматично через кілька годин після сеансу.</i>",
        reply_markup=_preview_back_kb(),
    )
    await callback.answer()


async def preview_review(callback: types.CallbackQuery) -> None:
    stars_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="⭐", callback_data="noop"),
            types.InlineKeyboardButton(text="⭐⭐", callback_data="noop"),
            types.InlineKeyboardButton(text="⭐⭐⭐", callback_data="noop"),
        ],
        [
            types.InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="noop"),
            types.InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="noop"),
        ],
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="c:after_booking")],
    ])
    await callback.message.edit_text(
        "💡 <i>Ось так виглядає запит відгуку (через 3 дні після сеансу):</i>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        + REVIEW_PREVIEW +
        "\n\n━━━━━━━━━━━━━━━━━━\n"
        "<i>Надсилається автоматично — клієнт ставить оцінку одним кліком.</i>",
        reply_markup=stars_kb,
    )
    await callback.answer()


# ── Back navigation ───────────────────────────────────────────────────────────

async def back_to_style(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingFSM.style)
    await _safe_edit(
        callback.message,
        "📋 <b>Запис на сеанс</b>\n\n"
        "<b>Крок 1 з 4</b> — Який стиль татуювання вас цікавить?",
        reply_markup=_styles_kb(),
    )
    await callback.answer()


async def back_to_zone(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(BookingFSM.zone)
    await _safe_edit(
        callback.message,
        f"📋 <b>Запис на сеанс</b>\n\n"
        f"✅ Стиль: <b>{data.get('style', '—')}</b>\n\n"
        f"<b>Крок 2 з 4</b> — Де буде тату і приблизний розмір?\n\n"
        f"<i>Наприклад: «Ліве передпліччя, 15×10 см»</i>",
        reply_markup=_zone_kb(),
    )
    await callback.answer()


async def back_to_reference(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(BookingFSM.reference)
    await _safe_edit(
        callback.message,
        f"📋 <b>Запис на сеанс</b>\n\n"
        f"✅ Стиль: <b>{data.get('style', '—')}</b>\n"
        f"✅ Зона: <b>{data.get('zone', '—')}</b>\n\n"
        f"<b>Крок 3 з 4</b> — Надішліть референс або опишіть ідею.\n\n"
        f"<i>Можна прикріпити фото, посилання або просто текст.\n"
        f"Якщо поки немає ідеї — напишіть «поки не знаю»</i>",
        reply_markup=_reference_kb(),
    )
    await callback.answer()


# ── Cancel ────────────────────────────────────────────────────────────────────

async def cancel_booking(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(
        callback.message,
        f"❌ Запис скасовано.\n\n"
        f"🖤 <b>{MASTER['full_name']}</b>\n{MASTER['bio']}",
        reply_markup=_client_menu_kb(),
    )
    await callback.answer("Скасовано")


# ── Noop (for non-interactive buttons) ───────────────────────────────────────

async def noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


# ── Register ──────────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.callback_query.register(client_menu,              F.data == "c:menu")
    dp.callback_query.register(show_portfolio,           F.data.startswith("c:portfolio:"))
    dp.callback_query.register(show_prices,              F.data == "c:prices")
    dp.callback_query.register(show_contacts,            F.data == "c:contacts")
    dp.callback_query.register(start_booking,            F.data == "c:book")
    dp.callback_query.register(start_booking_with_style, F.data.startswith("c:book_style:"))
    dp.callback_query.register(got_style,                F.data.startswith("c:style:"), BookingFSM.style)
    dp.callback_query.register(got_allergy,              F.data.startswith("c:allergy:"), BookingFSM.allergy)
    dp.callback_query.register(got_slot,                 F.data.startswith("c:slot:"), BookingFSM.slot)
    dp.callback_query.register(got_payment,              F.data == "c:paid", BookingFSM.awaiting_payment)
    dp.callback_query.register(show_after_booking,       F.data == "c:after_booking")
    dp.callback_query.register(preview_reminder,         F.data == "c:preview:reminder")
    dp.callback_query.register(preview_aftercare,        F.data == "c:preview:aftercare")
    dp.callback_query.register(preview_review,           F.data == "c:preview:review")
    dp.callback_query.register(back_to_style,            F.data == "c:back:zone",      BookingFSM.zone)
    dp.callback_query.register(back_to_zone,             F.data == "c:back:reference", BookingFSM.reference)
    dp.callback_query.register(back_to_reference,        F.data == "c:back:allergy",   BookingFSM.allergy)
    dp.callback_query.register(cancel_booking,           F.data == "c:cancel")
    dp.callback_query.register(noop,                     F.data == "noop")

    dp.message.register(got_zone,            BookingFSM.zone)
    dp.message.register(got_reference_photo, BookingFSM.reference, F.photo)
    dp.message.register(got_reference_text,  BookingFSM.reference)
