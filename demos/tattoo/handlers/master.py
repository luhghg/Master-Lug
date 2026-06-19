from pathlib import Path

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InputMediaPhoto

from db import get_booking
from demo_data import MASTER, PORTFOLIO, DEMO_BOOKINGS, DEMO_CLIENTS, STATS


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


def _master_menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📅 Записи", callback_data="m:bookings"),
            types.InlineKeyboardButton(text="👥 Клієнти", callback_data="m:clients"),
        ],
        [
            types.InlineKeyboardButton(text="🖼 Портфоліо", callback_data="m:portfolio:0"),
            types.InlineKeyboardButton(text="📊 Фінанси", callback_data="m:finance"),
        ],
        [
            types.InlineKeyboardButton(text="📬 Як виглядають сповіщення", callback_data="m:notify_preview"),
        ],
        [
            types.InlineKeyboardButton(text="⚙️ Налаштування", callback_data="m:settings"),
        ],
    ])


def _back_master_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Меню майстра", callback_data="m:menu")],
    ])


# ── Main menu ─────────────────────────────────────────────────────────────────

async def show_master_menu(message: types.Message) -> None:
    await message.answer(
        f"⚙️ <b>Панель майстра — {MASTER['full_name']}</b>\n\n"
        f"📍 {MASTER['city']}, {MASTER['specialization']}\n\n"
        f"Що хочете переглянути?",
        reply_markup=_master_menu_kb(),
    )


async def master_menu(callback: types.CallbackQuery) -> None:
    await _safe_edit(
        callback.message,
        f"⚙️ <b>Панель майстра — {MASTER['full_name']}</b>\n\n"
        f"📍 {MASTER['city']}, {MASTER['specialization']}\n\n"
        f"Що хочете переглянути?",
        reply_markup=_master_menu_kb(),
    )
    await callback.answer()


# ── Bookings ──────────────────────────────────────────────────────────────────

async def show_bookings(callback: types.CallbackQuery) -> None:
    user_booking = await get_booking(callback.from_user.id)

    all_bookings = list(DEMO_BOOKINGS)

    if user_booking:
        all_bookings = [{
            "id": "B-YOUR",
            "client": f"Ви (тест, {callback.from_user.first_name})",
            "username": f"@{callback.from_user.username or 'ваш_акаунт'}",
            "style": user_booking["style"],
            "zone": user_booking["zone"],
            "slot": user_booking["slot"],
            "deposit_status": "✅ 500 грн отримано (демо)",
            "status": "confirmed",
            "status_label": "✅ Підтверджено",
            "allergy": user_booking["allergy"],
            "reference": user_booking["reference"],
            "reference_file_id": user_booking.get("reference_file_id"),
        }] + all_bookings

    rows = []
    for b in all_bookings:
        label = f"{b['status_label']}  {b['client']}"
        rows.append([types.InlineKeyboardButton(text=label, callback_data=f"m:booking:{b['id']}")])
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="m:menu")])

    header = "📅 <b>Записи</b>\n\n"
    if user_booking:
        header += "⬆️ <i>Ваш тестовий запис (з режиму клієнта) вгорі списку</i>\n\n"

    for b in all_bookings:
        header += f"{b['status_label']}  <b>{b['client']}</b> — {b['slot']}\n"

    await _safe_edit(
        callback.message,
        header,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def show_booking_detail(callback: types.CallbackQuery) -> None:
    booking_id = callback.data.split(":", 2)[2]

    user_booking = await get_booking(callback.from_user.id)
    all_bookings_map = {b["id"]: b for b in DEMO_BOOKINGS}

    if booking_id == "B-YOUR" and user_booking:
        b = {
            "id": "B-YOUR",
            "client": f"{callback.from_user.first_name} (тест)",
            "username": f"@{callback.from_user.username or '—'}",
            "style": user_booking["style"],
            "zone": user_booking["zone"],
            "slot": user_booking["slot"],
            "deposit_status": "✅ 500 грн отримано (демо)",
            "status": "confirmed",
            "status_label": "✅ Підтверджено",
            "allergy": user_booking["allergy"],
            "reference": user_booking["reference"],
            "reference_file_id": user_booking.get("reference_file_id"),
        }
    elif booking_id in all_bookings_map:
        b = all_bookings_map[booking_id]
    else:
        await callback.answer("Запис не знайдено", show_alert=True)
        return

    text = (
        f"📋 <b>Запис {b['id']}</b>\n\n"
        f"👤 Клієнт: <b>{b['client']}</b> {b['username']}\n"
        f"🎨 Стиль: <b>{b['style']}</b>\n"
        f"📍 Зона: <b>{b['zone']}</b>\n"
        f"🖼 Референс: <b>{b['reference']}</b>\n"
        f"⚕️ Алергія: <b>{b['allergy']}</b>\n"
        f"📅 Час: <b>{b['slot']}</b>\n"
        f"💳 Депозит: <b>{b['deposit_status']}</b>\n\n"
        f"Статус: {b['status_label']}"
    )

    kb_rows = []
    if b["status"] == "pending":
        kb_rows.append([
            types.InlineKeyboardButton(text="✅ Підтвердити", callback_data=f"m:confirm:{b['id']}"),
            types.InlineKeyboardButton(text="❌ Відхилити", callback_data=f"m:reject:{b['id']}"),
        ])
    kb_rows.append([types.InlineKeyboardButton(text="◀️ До записів", callback_data="m:bookings")])
    kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)

    ref_file_id = b.get("reference_file_id")
    if ref_file_id:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(photo=ref_file_id, caption=text, reply_markup=kb)
    else:
        await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def confirm_booking(callback: types.CallbackQuery) -> None:
    booking_id = callback.data.split(":", 2)[2]
    await _safe_edit(
        callback.message,
        f"✅ <b>Запис {booking_id} підтверджено!</b>\n\n"
        f"Клієнту автоматично надіслано підтвердження з адресою студії.\n\n"
        f"<i>(В реальному боті клієнт одразу отримає повідомлення)</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ До записів", callback_data="m:bookings")],
        ]),
    )
    await callback.answer("✅ Підтверджено!")


async def reject_booking(callback: types.CallbackQuery) -> None:
    booking_id = callback.data.split(":", 2)[2]
    await _safe_edit(
        callback.message,
        f"❌ <b>Запис {booking_id} відхилено.</b>\n\n"
        f"Клієнту автоматично надіслано повідомлення про відхилення.\n\n"
        f"<i>(В реальному боті клієнт одразу отримає повідомлення)</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ До записів", callback_data="m:bookings")],
        ]),
    )
    await callback.answer("❌ Відхилено")


# ── Clients ───────────────────────────────────────────────────────────────────

async def show_clients(callback: types.CallbackQuery) -> None:
    rows = [
        [types.InlineKeyboardButton(
            text=f"{'⭐' * (c['rating'] or 0)} {c['name']}  ({c['visits']} {'візит' if c['visits'] == 1 else 'візити'})",
            callback_data=f"m:client:{i}",
        )]
        for i, c in enumerate(DEMO_CLIENTS)
    ]
    rows.append([types.InlineKeyboardButton(text="◀️ Меню", callback_data="m:menu")])

    text = f"👥 <b>Клієнти</b>\n\nВсього: {len(DEMO_CLIENTS)} клієнти у демо\n\n"
    for c in DEMO_CLIENTS:
        rating_str = f"⭐ {c['rating']}/5" if c["rating"] else "без відгуку"
        text += f"• <b>{c['name']}</b> {c['username']} — {c['visits']} візити, {rating_str}\n"

    await _safe_edit(callback.message, text,
                     reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def show_client_detail(callback: types.CallbackQuery) -> None:
    idx = int(callback.data.split(":")[2])
    c = DEMO_CLIENTS[idx]

    rating_str = f"{'⭐' * c['rating']} ({c['rating']}/5)" if c["rating"] else "ще немає відгуку"
    history_str = "\n".join(f"• {h}" for h in c["history"]) if c["history"] else "• (записів ще не було)"

    text = (
        f"👤 <b>{c['name']}</b>\n"
        f"Telegram: {c['username']}\n\n"
        f"📊 Візитів: <b>{c['visits']}</b>\n"
        f"⭐ Рейтинг: {rating_str}\n\n"
        f"📝 <b>Нотатка майстра:</b>\n{c['note']}\n\n"
        f"🗓 <b>Історія:</b>\n{history_str}"
    )

    await _safe_edit(callback.message, text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ До клієнтів", callback_data="m:clients")],
    ]))
    await callback.answer()


# ── Portfolio (master view) ───────────────────────────────────────────────────

async def show_portfolio_master(callback: types.CallbackQuery) -> None:
    idx = int(callback.data.split(":")[2])
    item = PORTFOLIO[idx]
    total = len(PORTFOLIO)

    nav = []
    if idx > 0:
        nav.append(types.InlineKeyboardButton(text="◀️", callback_data=f"m:portfolio:{idx - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{idx + 1}/{total}", callback_data="noop"))
    if idx < total - 1:
        nav.append(types.InlineKeyboardButton(text="▶️", callback_data=f"m:portfolio:{idx + 1}"))

    caption = (
        f"{item['emoji']} <b>{item['style']} — {item['title']}</b>\n\n"
        f"📝 {item['description']}\n\n"
        f"⏱ Час: <b>{item['hours']}</b>  |  💰 Ціна: <b>{item['price']}</b>"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text="◀️ Меню", callback_data="m:menu")],
    ])

    photo_path = item.get("photo_path")
    if photo_path and Path(photo_path).exists():
        if callback.message.photo:
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=FSInputFile(photo_path),
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=kb,
            )
        else:
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
            f"📸 <i>[PHOTO PLACEHOLDER: {item['style']} — {item['title']}]\n"
            f"← Замініть на реальне фото роботи перед показом</i>\n\n"
            f"📝 {item['description']}\n\n"
            f"⏱ Час: <b>{item['hours']}</b>  |  💰 Ціна: <b>{item['price']}</b>"
        )
        if callback.message.photo:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(text, reply_markup=kb)
        else:
            await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


# ── Finance ───────────────────────────────────────────────────────────────────

async def show_finance(callback: types.CallbackQuery) -> None:
    s = STATS
    text = (
        f"📊 <b>Статистика — {s['month']}</b>\n\n"
        f"📅 Записів всього: <b>{s['bookings']}</b>\n"
        f"   ✅ Підтверджено: {s['confirmed']}\n"
        f"   ✔️ Завершено: {s['completed']}\n"
        f"   ⏳ Очікує: {s['pending']}\n"
        f"   ❌ Скасовано: {s['cancelled']}\n\n"
        f"⭐ Середній рейтинг: <b>{s['avg_rating']}</b>\n\n"
        f"💳 Депозитів отримано: <b>{s['deposits']}</b>\n"
        f"💰 Орієнтовний дохід: <b>{s['est_income']}</b>\n\n"
        f"🏆 Топ стиль: <b>{s['top_style']}</b>\n\n"
        f"👥 Нові клієнти: {s['new_clients']}\n"
        f"🔁 Повторні клієнти: {s['returning_clients']}\n\n"
        f"<i>В реальному боті цифри рахуються автоматично з ваших даних.</i>"
    )
    await _safe_edit(callback.message, text, reply_markup=_back_master_kb())
    await callback.answer()


# ── Notification preview ──────────────────────────────────────────────────────

async def show_notify_preview(callback: types.CallbackQuery) -> None:
    text = (
        "📬 <b>Як виглядають сповіщення майстру</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔔 <b>Нова заявка!</b>\n\n"
        "👤 Клієнт: <b>Марія Коваленко</b> @maria_k\n"
        "🎨 Стиль: Реалізм\n"
        "📍 Зона: Ліве передпліччя, ~15 см\n"
        "🖼 Референс: Фото з Pinterest\n"
        "⚕️ Алергія: Немає\n"
        "📅 Бажаний час: Пт 27 червня, 10:00\n"
        "💳 Депозит: ⏳ очікується\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Кнопки «Підтвердити» і «Відхилити» — одним кліком прямо тут.</i>\n\n"
        "Всі дії — у Telegram, без жодних сторонніх сайтів."
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Підтвердити", callback_data="noop"),
            types.InlineKeyboardButton(text="❌ Відхилити", callback_data="noop"),
        ],
        [types.InlineKeyboardButton(text="◀️ Меню", callback_data="m:menu")],
    ])
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


# ── Settings (stub) ───────────────────────────────────────────────────────────

async def show_settings(callback: types.CallbackQuery) -> None:
    text = (
        "⚙️ <b>Налаштування</b>\n\n"
        "В повній версії тут:\n\n"
        "• 👤 Ім'я та опис майстра\n"
        "• 📅 Розклад і тривалість сеансів\n"
        "• 💳 Реквізити для депозиту\n"
        "• 📋 Питання анкети клієнта\n"
        "• 💬 Тексти повідомлень і нагадувань\n"
        "• 🎨 Стилі та послуги\n"
        "• 🚫 Вихідні та відпустки\n\n"
        "<i>Все налаштовується без програміста, прямо тут у боті.</i>"
    )
    await _safe_edit(callback.message, text, reply_markup=_back_master_kb())
    await callback.answer()


# ── Register ──────────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.callback_query.register(master_menu,           F.data == "m:menu")
    dp.callback_query.register(show_bookings,         F.data == "m:bookings")
    dp.callback_query.register(show_booking_detail,   F.data.startswith("m:booking:"))
    dp.callback_query.register(confirm_booking,       F.data.startswith("m:confirm:"))
    dp.callback_query.register(reject_booking,        F.data.startswith("m:reject:"))
    dp.callback_query.register(show_clients,          F.data == "m:clients")
    dp.callback_query.register(show_client_detail,    F.data.startswith("m:client:"))
    dp.callback_query.register(show_portfolio_master, F.data.startswith("m:portfolio:"))
    dp.callback_query.register(show_finance,          F.data == "m:finance")
    dp.callback_query.register(show_notify_preview,   F.data == "m:notify_preview")
    dp.callback_query.register(show_settings,         F.data == "m:settings")
