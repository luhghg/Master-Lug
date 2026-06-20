"""Full settings panel for TATTOO niche — available after wizard completion."""
import logging

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import ApptSchedule
from app.models.bot_config import BotConfig
from app.models.tattoo import TattooService
from app.services.config_service import get_cfg, get_json, set_cfg, set_json
from app.bot.handlers.niche.tattoo.wizard import (
    TTT_ONBOARDING_DONE, TTT_MASTER_NAME, TTT_MASTER_BIO, TTT_MASTER_CITY,
    TTT_STYLES, TTT_DEPOSIT_ENABLED, TTT_DEPOSIT_AMOUNT, TTT_CARD_NUMBER,
    TTT_DEPOSIT_PURPOSE, TTT_QUESTIONNAIRE, TTT_REMINDERS,
    TTT_MSG_WELCOME, TTT_MSG_CONFIRM, TTT_MSG_REMINDER_TPL, TTT_MSG_AFTERCARE,
    TTT_MSG_REVIEW_TPL, TTT_MSG_DEPOSIT, TTT_MIN_AGE_ENABLED, TTT_MIN_AGE_TEXT,
    TTT_CANCEL_HOURS, _TMPL, _QUEST_FIELDS, _REMINDER_FIELDS, _MSG_LABELS,
    _STYLE_OPTIONS, _DAYS_SHORT,
)

logger = logging.getLogger(__name__)


# ── FSM ────────────────────────────────────────────────────────────────────────

class TattooSettingsFSM(StatesGroup):
    s_name        = State()
    s_bio         = State()
    s_city        = State()
    s_sched_days  = State()
    s_sched_start = State()
    s_sched_end   = State()
    s_sched_duration = State()
    s_sched_buffer   = State()
    s_svc_name    = State()
    s_svc_price   = State()
    s_svc_desc    = State()
    s_dep_amount  = State()
    s_dep_card    = State()
    s_dep_purpose = State()
    s_msg_edit    = State()
    s_age_text    = State()
    s_cancel_hours = State()


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


def _back_to_settings_btn() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="◀️ Налаштування", callback_data="ttts_menu")


def _back_to_admin_btn() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="◀️ Панель майстра", callback_data="tttm_admin:home")


# ── Main menu ──────────────────────────────────────────────────────────────────

async def show_settings_menu(
    callback_or_message,
    session: AsyncSession | None = None,
    registered_bot_id: int | None = None,
) -> None:
    text = "⚙️ <b>Налаштування</b>\n\nОберіть розділ:"
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👤 Профіль майстра",        callback_data="ttts_prof")],
        [types.InlineKeyboardButton(text="🗓 Розклад та вихідні",     callback_data="ttts_schedule")],
        [types.InlineKeyboardButton(text="🎨 Послуги та ціни",        callback_data="ttts_services")],
        [types.InlineKeyboardButton(text="🖼 Стилі",                  callback_data="ttts_styles")],
        [types.InlineKeyboardButton(text="💳 Депозит",                callback_data="ttts_deposit")],
        [types.InlineKeyboardButton(text="📋 Анкета клієнта",         callback_data="ttts_quest")],
        [types.InlineKeyboardButton(text="🔔 Нагадування",            callback_data="ttts_reminders")],
        [types.InlineKeyboardButton(text="💬 Шаблони повідомлень",    callback_data="ttts_messages")],
        [types.InlineKeyboardButton(text="🚫 Обмеження",              callback_data="ttts_restrict")],
        [_back_to_admin_btn()],
        [types.InlineKeyboardButton(text="🔄 Скинути налаштування",   callback_data="ttts_reset")],
    ])
    if isinstance(callback_or_message, types.CallbackQuery):
        await _safe_edit(callback_or_message.message, text, reply_markup=kb)
        await callback_or_message.answer()
    else:
        await callback_or_message.answer(text, reply_markup=kb)


async def settings_menu_cb(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await show_settings_menu(callback, session, registered_bot_id)


# ── Profile ────────────────────────────────────────────────────────────────────

async def settings_profile(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    name = await get_cfg(session, registered_bot_id, TTT_MASTER_NAME, "—")
    bio  = await get_cfg(session, registered_bot_id, TTT_MASTER_BIO,  "—")
    city = await get_cfg(session, registered_bot_id, TTT_MASTER_CITY, "—")
    text = (
        f"👤 <b>Профіль майстра</b>\n\n"
        f"Ім'я: <b>{name}</b>\n"
        f"Місто: <b>{city}</b>\n"
        f"Опис: {bio}"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✏️ Ім'я/назва",   callback_data="ttts_prof_edit:name")],
        [types.InlineKeyboardButton(text="✏️ Опис",         callback_data="ttts_prof_edit:bio")],
        [types.InlineKeyboardButton(text="✏️ Місто",        callback_data="ttts_prof_edit:city")],
        [_back_to_settings_btn()],
    ])
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def settings_profile_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    field = callback.data.split(":")[1]
    prompts = {
        "name": ("✏️ Введіть нове ім'я або назву студії:", TattooSettingsFSM.s_name),
        "bio":  ("✏️ Введіть новий опис (до 300 символів):", TattooSettingsFSM.s_bio),
        "city": ("✏️ Введіть нове місто:", TattooSettingsFSM.s_city),
    }
    if field not in prompts:
        await callback.answer()
        return
    text, fsm_state = prompts[field]
    await state.update_data(s_prof_field=field)
    await state.set_state(fsm_state)
    await callback.answer()
    await _safe_edit(callback.message, text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_prof")],
    ]))


async def settings_profile_save(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    field = data.get("s_prof_field", "")
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Введіть значення:")
        return
    if field == "name" and len(value) > 64:
        await message.answer("Максимум 64 символи:")
        return
    if field == "bio" and len(value) > 300:
        await message.answer("Максимум 300 символів:")
        return
    key_map = {"name": TTT_MASTER_NAME, "bio": TTT_MASTER_BIO, "city": TTT_MASTER_CITY}
    if field in key_map:
        await set_cfg(session, registered_bot_id, key_map[field], value)
    await state.clear()
    await message.answer("✅ Збережено.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Профіль", callback_data="ttts_prof")],
    ]))


# ── Schedule ───────────────────────────────────────────────────────────────────

async def settings_schedule(
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
            lines.append(f"✅ {['Пн','Вт','Ср','Чт','Пт','Сб','Нд'][dow]}: {s.start_time}–{s.end_time} по {s.slot_duration_min}хв")
        else:
            lines.append(f"🔴 {['Пн','Вт','Ср','Чт','Пт','Сб','Нд'][dow]}: вихідний")

    text = "🗓 <b>Розклад</b>\n\n" + "\n".join(lines)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✏️ Змінити розклад", callback_data="ttts_sched_edit")],
        [types.InlineKeyboardButton(text="🚫 Відпустка / блокування", callback_data="tttm_blocked")],
        [_back_to_settings_btn()],
    ])
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def settings_sched_edit(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooSettingsFSM.s_sched_days)
    await callback.answer()
    data = await state.get_data()
    selected = data.get("s_sched_days", [])
    row1 = []
    for i in range(7):
        mark = "✅" if i in selected else "◻️"
        row1.append(types.InlineKeyboardButton(
            text=f"{mark}{_DAYS_SHORT[i]}", callback_data=f"ttts_sched_day_tog:{i}"
        ))
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        row1[:4], row1[4:],
        [types.InlineKeyboardButton(text="✅ Підтвердити дні", callback_data="ttts_sched_days_done")],
        [_back_to_settings_btn()],
    ])
    await _safe_edit(callback.message, "🗓 <b>Розклад</b>\n\nОберіть робочі дні:", reply_markup=kb)


async def settings_sched_day_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    dow = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected = list(data.get("s_sched_days", []))
    if dow in selected:
        selected.remove(dow)
    else:
        selected.append(dow)
    await state.update_data(s_sched_days=selected)
    row1 = []
    for i in range(7):
        mark = "✅" if i in selected else "◻️"
        row1.append(types.InlineKeyboardButton(
            text=f"{mark}{_DAYS_SHORT[i]}", callback_data=f"ttts_sched_day_tog:{i}"
        ))
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        row1[:4], row1[4:],
        [types.InlineKeyboardButton(text="✅ Підтвердити дні", callback_data="ttts_sched_days_done")],
        [_back_to_settings_btn()],
    ])
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


def _s_time_kb(start_h: int, end_h: int, step_min: int, cb_prefix: str) -> types.InlineKeyboardMarkup:
    times = []
    cur = start_h * 60
    while cur <= end_h * 60:
        times.append(f"{cur // 60:02d}:{cur % 60:02d}")
        cur += step_min
    rows = []
    for i in range(0, len(times), 3):
        rows.append([
            types.InlineKeyboardButton(text=t, callback_data=f"{cb_prefix}:{t}")
            for t in times[i:i+3]
        ])
    rows.append([_back_to_settings_btn()])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def settings_sched_days_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("s_sched_days"):
        await callback.answer("Оберіть хоча б 1 день!", show_alert=True)
        return
    await state.set_state(TattooSettingsFSM.s_sched_start)
    await callback.answer()
    await _safe_edit(
        callback.message,
        "🗓 <b>Розклад</b>\n\nОберіть час початку роботи:",
        reply_markup=_s_time_kb(7, 20, 30, "ttts_sched_start"),
    )


async def settings_sched_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    t = callback.data.split(":")[1] + ":" + callback.data.split(":")[2]
    await state.update_data(s_sched_start=t)
    await state.set_state(TattooSettingsFSM.s_sched_end)
    await callback.answer()
    await _safe_edit(
        callback.message,
        "🗓 <b>Розклад</b>\n\nОберіть час закінчення роботи:",
        reply_markup=_s_time_kb(8, 22, 30, "ttts_sched_end"),
    )


async def settings_sched_end(callback: types.CallbackQuery, state: FSMContext) -> None:
    t = callback.data.split(":")[1] + ":" + callback.data.split(":")[2]
    await state.update_data(s_sched_end=t)
    await state.set_state(TattooSettingsFSM.s_sched_duration)
    opts = [("30 хв", 30), ("45 хв", 45), ("60 хв", 60), ("90 хв", 90), ("120 хв", 120), ("180 хв", 180)]
    rows = [
        [types.InlineKeyboardButton(text=lbl, callback_data=f"ttts_sched_dur:{m}") for lbl, m in opts[:3]],
        [types.InlineKeyboardButton(text=lbl, callback_data=f"ttts_sched_dur:{m}") for lbl, m in opts[3:]],
        [_back_to_settings_btn()],
    ]
    await callback.answer()
    await _safe_edit(
        callback.message,
        "🗓 <b>Розклад</b>\n\nТривалість сеансу:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def settings_sched_duration(callback: types.CallbackQuery, state: FSMContext) -> None:
    mins = int(callback.data.split(":")[1])
    await state.update_data(s_sched_duration=mins)
    await state.set_state(TattooSettingsFSM.s_sched_buffer)
    opts = [("0 хв", 0), ("15 хв", 15), ("30 хв", 30), ("45 хв", 45), ("60 хв", 60)]
    row = [types.InlineKeyboardButton(text=lbl, callback_data=f"ttts_sched_buf:{m}") for lbl, m in opts]
    await callback.answer()
    await _safe_edit(
        callback.message,
        "🗓 <b>Розклад</b>\n\nПауза між сеансами:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[row[:3], row[3:], [_back_to_settings_btn()]]),
    )


async def settings_sched_buffer(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    buf = int(callback.data.split(":")[1])
    data = await state.get_data()
    days = data.get("s_sched_days", [])
    start = data.get("s_sched_start", "10:00")
    end   = data.get("s_sched_end", "20:00")
    dur   = data.get("s_sched_duration", 60)

    for dow in days:
        stmt = (
            pg_insert(ApptSchedule)
            .values(
                bot_id=registered_bot_id,
                day_of_week=dow,
                start_time=start,
                end_time=end,
                slot_duration_min=dur,
                buffer_min=buf,
                is_active=True,
            )
            .on_conflict_do_update(
                constraint="uq_appt_schedule",
                set_={
                    "start_time": start,
                    "end_time": end,
                    "slot_duration_min": dur,
                    "buffer_min": buf,
                    "is_active": True,
                },
            )
        )
        await session.execute(stmt)
    await session.commit()
    await state.clear()
    await callback.answer("✅ Розклад збережено!")
    await _safe_edit(
        callback.message,
        "✅ Розклад оновлено.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ Розклад", callback_data="ttts_schedule")],
        ]),
    )


# ── Services ───────────────────────────────────────────────────────────────────

async def settings_services(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    services = (await session.execute(
        select(TattooService)
        .where(TattooService.bot_id == registered_bot_id)
        .order_by(TattooService.position)
    )).scalars().all()

    if not services:
        text = "🎨 <b>Послуги та ціни</b>\n\nПослуг ще немає."
    else:
        lines = []
        for s in services:
            status = "✅" if s.is_active else "🔴"
            lines.append(f"{status} <b>{s.name}</b> — {s.price}")
        text = "🎨 <b>Послуги та ціни</b>\n\n" + "\n".join(lines)

    rows = []
    for s in services:
        vis_label = "🙈 Сховати" if s.is_active else "👁 Показати"
        rows.append([
            types.InlineKeyboardButton(text=f"✏️ {s.name[:20]}", callback_data=f"ttts_svc_edit:{s.id}"),
            types.InlineKeyboardButton(text=vis_label, callback_data=f"ttts_svc_toggle:{s.id}"),
            types.InlineKeyboardButton(text="🗑", callback_data=f"ttts_svc_del:{s.id}"),
        ])
    rows.append([types.InlineKeyboardButton(text="➕ Додати послугу", callback_data="ttts_svc_add")])
    rows.append([_back_to_settings_btn()])

    await _safe_edit(callback.message, text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def settings_svc_add(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooSettingsFSM.s_svc_name)
    await callback.answer()
    await _safe_edit(callback.message, "Введіть назву нової послуги:", reply_markup=types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_services")]]
    ))


async def settings_svc_name_input(message: types.Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Введіть назву:")
        return
    await state.update_data(s_svc_name=name, s_svc_edit_id=None)
    await state.set_state(TattooSettingsFSM.s_svc_price)
    await message.answer("Ціна (грн або діапазон):")


async def settings_svc_price_input(message: types.Message, state: FSMContext) -> None:
    price = message.text.strip() if message.text else ""
    if not price:
        await message.answer("Введіть ціну:")
        return
    await state.update_data(s_svc_price=price)
    await state.set_state(TattooSettingsFSM.s_svc_desc)
    await message.answer("Опис (або «-» щоб пропустити):", reply_markup=types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="⏩ Пропустити", callback_data="ttts_svc_desc_skip")]]
    ))


async def settings_svc_desc_skip(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await _s_save_service(callback.message, state, session, registered_bot_id, desc=None, edit=True)
    await callback.answer()


async def settings_svc_desc_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    desc = message.text.strip() if message.text else None
    if desc == "-":
        desc = None
    await _s_save_service(message, state, session, registered_bot_id, desc=desc, edit=False)


async def _s_save_service(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot_id: int,
    desc: str | None,
    edit: bool,
) -> None:
    data = await state.get_data()
    edit_id = data.get("s_svc_edit_id")
    name = data.get("s_svc_name", "")
    price = data.get("s_svc_price", "")

    if edit_id:
        svc = await session.get(TattooService, int(edit_id))
        if svc and svc.bot_id == bot_id:
            svc.name = name
            svc.price = price
            svc.description = desc
            await session.commit()
    else:
        count = (await session.execute(
            select(TattooService).where(TattooService.bot_id == bot_id)
        )).scalars().all()
        session.add(TattooService(
            bot_id=bot_id,
            name=name,
            price=price,
            description=desc,
            position=len(count),
        ))
        await session.commit()

    await state.clear()
    text = "✅ Послугу збережено."
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Послуги", callback_data="ttts_services")],
    ])
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


async def settings_svc_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    svc_id = int(callback.data.split(":")[1])
    svc = await session.get(TattooService, svc_id)
    if not svc or svc.bot_id != registered_bot_id:
        await callback.answer("Не знайдено.", show_alert=True)
        return
    await state.update_data(s_svc_edit_id=svc_id, s_svc_name=svc.name, s_svc_price=svc.price)
    await state.set_state(TattooSettingsFSM.s_svc_name)
    await callback.answer()
    await _safe_edit(callback.message, f"✏️ Нова назва для «{svc.name}»:", reply_markup=types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_services")]]
    ))


async def settings_svc_toggle(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    svc_id = int(callback.data.split(":")[1])
    svc = await session.get(TattooService, svc_id)
    if svc and svc.bot_id == registered_bot_id:
        svc.is_active = not svc.is_active
        await session.commit()
    await callback.answer("✅ Оновлено.")
    callback.data = "ttts_services"
    await settings_services(callback, session, registered_bot_id)


async def settings_svc_delete(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    svc_id = int(callback.data.split(":")[1])
    svc = await session.get(TattooService, svc_id)
    if svc and svc.bot_id == registered_bot_id:
        await session.delete(svc)
        await session.commit()
    await callback.answer("🗑 Видалено.")
    callback.data = "ttts_services"
    await settings_services(callback, session, registered_bot_id)


# ── Styles ─────────────────────────────────────────────────────────────────────

async def settings_styles(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    selected = await get_json(session, registered_bot_id, TTT_STYLES, [])
    await _safe_edit(
        callback.message,
        "🖼 <b>Стилі роботи</b>\n\nОберіть стилі:",
        reply_markup=_s_styles_kb(selected),
    )
    await callback.answer()


def _s_styles_kb(selected: list) -> types.InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(_STYLE_OPTIONS), 2):
        row = []
        for opt in _STYLE_OPTIONS[i:i+2]:
            mark = "✅" if opt in selected else "◻️"
            row.append(types.InlineKeyboardButton(
                text=f"{mark} {opt}", callback_data=f"ttts_style_tog:{opt}"
            ))
        rows.append(row)
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти", callback_data="ttts_styles_save")])
    rows.append([_back_to_settings_btn()])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def settings_style_toggle(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    opt = callback.data.split(":", 1)[1]
    selected = await get_json(session, registered_bot_id, TTT_STYLES, [])
    selected = list(selected)
    if opt in selected:
        selected.remove(opt)
    else:
        selected.append(opt)
    await set_json(session, registered_bot_id, TTT_STYLES, selected)
    try:
        await callback.message.edit_reply_markup(reply_markup=_s_styles_kb(selected))
    except Exception:
        pass
    await callback.answer()


async def settings_styles_save(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    selected = await get_json(session, registered_bot_id, TTT_STYLES, [])
    if not selected:
        await callback.answer("Оберіть хоча б 1 стиль!", show_alert=True)
        return
    await callback.answer("✅ Стилі збережено!")
    await _safe_edit(
        callback.message,
        "✅ Стилі збережено.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [_back_to_settings_btn()],
        ]),
    )


# ── Deposit ────────────────────────────────────────────────────────────────────

async def settings_deposit(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    enabled = await get_cfg(session, registered_bot_id, TTT_DEPOSIT_ENABLED, "false")
    amount  = await get_cfg(session, registered_bot_id, TTT_DEPOSIT_AMOUNT, "500")
    card    = await get_cfg(session, registered_bot_id, TTT_CARD_NUMBER, "—")
    purpose = await get_cfg(session, registered_bot_id, TTT_DEPOSIT_PURPOSE, "—")
    is_on = enabled == "true"
    toggle_text = "🔴 Вимкнути депозит" if is_on else "🟢 Увімкнути депозит"
    toggle_cb   = "ttts_dep_off" if is_on else "ttts_dep_on"
    text = (
        f"💳 <b>Депозит</b>\n\n"
        f"Статус: {'✅ Увімкнений' if is_on else '🔴 Вимкнений'}\n"
        f"Сума: <b>{amount} грн</b>\n"
        f"Картка: <code>{card}</code>\n"
        f"Призначення: {purpose}"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
        [types.InlineKeyboardButton(text="✏️ Сума депозиту",   callback_data="ttts_dep_edit:amount")],
        [types.InlineKeyboardButton(text="✏️ Номер картки",    callback_data="ttts_dep_edit:card")],
        [types.InlineKeyboardButton(text="✏️ Призначення",     callback_data="ttts_dep_edit:purpose")],
        [_back_to_settings_btn()],
    ])
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def settings_dep_on(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, TTT_DEPOSIT_ENABLED, "true")
    await callback.answer("✅ Депозит увімкнено.")
    callback.data = "ttts_deposit"
    await settings_deposit(callback, session, registered_bot_id)


async def settings_dep_off(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, TTT_DEPOSIT_ENABLED, "false")
    await callback.answer("🔴 Депозит вимкнено.")
    callback.data = "ttts_deposit"
    await settings_deposit(callback, session, registered_bot_id)


async def settings_dep_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    field = callback.data.split(":")[1]
    prompts = {
        "amount":  ("Введіть нову суму депозиту (грн):", TattooSettingsFSM.s_dep_amount),
        "card":    ("Введіть номер картки:", TattooSettingsFSM.s_dep_card),
        "purpose": ("Введіть призначення платежу:", TattooSettingsFSM.s_dep_purpose),
    }
    if field not in prompts:
        await callback.answer()
        return
    text, fsm_state = prompts[field]
    await state.update_data(s_dep_field=field)
    await state.set_state(fsm_state)
    await callback.answer()
    await _safe_edit(callback.message, text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_deposit")],
    ]))


async def settings_dep_save(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    field = data.get("s_dep_field", "")
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Введіть значення:")
        return
    if field == "amount":
        try:
            int(value)
        except ValueError:
            await message.answer("Введіть тільки число:")
            return
        await set_cfg(session, registered_bot_id, TTT_DEPOSIT_AMOUNT, value)
    elif field == "card":
        await set_cfg(session, registered_bot_id, TTT_CARD_NUMBER, value)
    elif field == "purpose":
        await set_cfg(session, registered_bot_id, TTT_DEPOSIT_PURPOSE, value)
    await state.clear()
    await message.answer("✅ Збережено.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Депозит", callback_data="ttts_deposit")],
    ]))


# ── Questionnaire ──────────────────────────────────────────────────────────────

async def settings_quest(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    quest = await get_json(session, registered_bot_id, TTT_QUESTIONNAIRE, {k: True for k, _ in _QUEST_FIELDS})
    rows = []
    for key, label in _QUEST_FIELDS:
        on = quest.get(key, True)
        mark = "✅" if on else "◻️"
        rows.append([types.InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"ttts_quest_tog:{key}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти", callback_data="ttts_quest_save")])
    rows.append([_back_to_settings_btn()])
    await _safe_edit(
        callback.message,
        "📋 <b>Анкета клієнта</b>\n\nОберіть питання для клієнтів:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def settings_quest_toggle(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    key = callback.data.split(":")[1]
    quest = await get_json(session, registered_bot_id, TTT_QUESTIONNAIRE, {k: True for k, _ in _QUEST_FIELDS})
    quest[key] = not quest.get(key, True)
    await set_json(session, registered_bot_id, TTT_QUESTIONNAIRE, quest)
    rows = []
    for k, label in _QUEST_FIELDS:
        on = quest.get(k, True)
        mark = "✅" if on else "◻️"
        rows.append([types.InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"ttts_quest_tog:{k}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти", callback_data="ttts_quest_save")])
    rows.append([_back_to_settings_btn()])
    try:
        await callback.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass
    await callback.answer()


async def settings_quest_save(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await callback.answer("✅ Анкету збережено!")
    await _safe_edit(
        callback.message,
        "✅ Анкету збережено.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_back_to_settings_btn()]]),
    )


# ── Reminders ──────────────────────────────────────────────────────────────────

async def settings_reminders(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    rems = await get_json(session, registered_bot_id, TTT_REMINDERS, {k: True for k, _ in _REMINDER_FIELDS})
    rows = []
    for key, label in _REMINDER_FIELDS:
        on = rems.get(key, True)
        mark = "✅" if on else "◻️"
        rows.append([types.InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"ttts_rem_tog:{key}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти", callback_data="ttts_rem_save")])
    rows.append([_back_to_settings_btn()])
    await _safe_edit(
        callback.message,
        "🔔 <b>Нагадування</b>\n\nОберіть активні нагадування:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def settings_rem_toggle(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    key = callback.data.split(":")[1]
    rems = await get_json(session, registered_bot_id, TTT_REMINDERS, {k: True for k, _ in _REMINDER_FIELDS})
    rems[key] = not rems.get(key, True)
    await set_json(session, registered_bot_id, TTT_REMINDERS, rems)
    rows = []
    for k, label in _REMINDER_FIELDS:
        on = rems.get(k, True)
        mark = "✅" if on else "◻️"
        rows.append([types.InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"ttts_rem_tog:{k}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти", callback_data="ttts_rem_save")])
    rows.append([_back_to_settings_btn()])
    try:
        await callback.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        pass
    await callback.answer()


async def settings_rem_save(
    callback: types.CallbackQuery,
) -> None:
    await callback.answer("✅ Нагадування збережено!")
    await _safe_edit(
        callback.message,
        "✅ Нагадування збережено.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_back_to_settings_btn()]]),
    )


# ── Messages ───────────────────────────────────────────────────────────────────

async def settings_messages(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    rows = []
    for key, label in _MSG_LABELS:
        current = await get_cfg(session, registered_bot_id, key, _TMPL.get(key, ""))
        preview = (current or "")[:50] + ("…" if len(current or "") > 50 else "")
        rows.append([types.InlineKeyboardButton(
            text=f"✏️ {label}: {preview}",
            callback_data=f"ttts_msg_edit:{key}",
        )])
    rows.append([_back_to_settings_btn()])
    await _safe_edit(
        callback.message,
        "💬 <b>Шаблони повідомлень</b>\n\nНатисніть щоб редагувати:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def settings_msg_edit(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    key = callback.data.split(":", 1)[1]
    label = next((lbl for k, lbl in _MSG_LABELS if k == key), key)
    current = await get_cfg(session, registered_bot_id, key, _TMPL.get(key, ""))
    await state.update_data(s_msg_key=key)
    await state.set_state(TattooSettingsFSM.s_msg_edit)
    await callback.answer()
    await _safe_edit(
        callback.message,
        f"✏️ <b>{label}</b>\n\nПоточний текст:\n<code>{current}</code>\n\nВведіть новий текст або скиньте до стандартного:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Скинути до стандартного", callback_data=f"ttts_msg_reset:{key}")],
            [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_messages")],
        ]),
    )


async def settings_msg_reset(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    state: FSMContext,
) -> None:
    key = callback.data.split(":", 1)[1]
    default = _TMPL.get(key, "")
    if default:
        await set_cfg(session, registered_bot_id, key, default)
    await state.clear()
    await callback.answer("🔄 Скинуто до стандартного!")
    callback.data = "ttts_messages"
    await settings_messages(callback, session, registered_bot_id)


async def settings_msg_save(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    key = data.get("s_msg_key", "")
    text = message.text.strip() if message.text else ""
    if not text or not key:
        await message.answer("Введіть текст повідомлення:")
        return
    await set_cfg(session, registered_bot_id, key, text)
    await state.clear()
    await message.answer("✅ Збережено.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Шаблони", callback_data="ttts_messages")],
    ]))


# ── Restrictions ───────────────────────────────────────────────────────────────

async def settings_restrict(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    age_enabled = await get_cfg(session, registered_bot_id, TTT_MIN_AGE_ENABLED, "false")
    age_text    = await get_cfg(session, registered_bot_id, TTT_MIN_AGE_TEXT, "Послуга для осіб від 18 років.")
    cancel_h    = await get_cfg(session, registered_bot_id, TTT_CANCEL_HOURS, "24")
    is_age_on = age_enabled == "true"
    text = (
        f"🚫 <b>Обмеження</b>\n\n"
        f"Мінімальний вік: {'✅ ' + age_text if is_age_on else '🔴 Вимкнено'}\n"
        f"Безкоштовне скасування за: <b>{cancel_h} год</b>"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="🔴 Вимкнути вік" if is_age_on else "✅ Увімкнути мін. вік",
            callback_data="ttts_age_off" if is_age_on else "ttts_age_on",
        )],
        [types.InlineKeyboardButton(text="✏️ Текст про вік",          callback_data="ttts_age_edit")],
        [types.InlineKeyboardButton(text="✏️ Години скасування",      callback_data="ttts_cancel_edit")],
        [_back_to_settings_btn()],
    ])
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def settings_age_on(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, TTT_MIN_AGE_ENABLED, "true")
    await callback.answer("✅ Обмеження віку увімкнено.")
    callback.data = "ttts_restrict"
    await settings_restrict(callback, session, registered_bot_id)


async def settings_age_off(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, TTT_MIN_AGE_ENABLED, "false")
    await callback.answer("🔴 Обмеження віку вимкнено.")
    callback.data = "ttts_restrict"
    await settings_restrict(callback, session, registered_bot_id)


async def settings_age_edit(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooSettingsFSM.s_age_text)
    await callback.answer()
    await _safe_edit(callback.message, "✏️ Введіть текст про обмеження віку:", reply_markup=types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_restrict")]]
    ))


async def settings_age_text_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("Введіть текст:")
        return
    await set_cfg(session, registered_bot_id, TTT_MIN_AGE_TEXT, text)
    await state.clear()
    await message.answer("✅ Збережено.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Обмеження", callback_data="ttts_restrict")],
    ]))


async def settings_cancel_edit(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooSettingsFSM.s_cancel_hours)
    await callback.answer()
    await _safe_edit(callback.message, "✏️ Безкоштовне скасування за скільки годин? (введіть число):",
                     reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                         [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="ttts_restrict")]
                     ]))


async def settings_cancel_hours_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    try:
        hours = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("Введіть число годин:")
        return
    await set_cfg(session, registered_bot_id, TTT_CANCEL_HOURS, str(hours))
    await state.clear()
    await message.answer("✅ Збережено.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Обмеження", callback_data="ttts_restrict")],
    ]))


# ── Reset ──────────────────────────────────────────────────────────────────────

async def settings_reset(
    callback: types.CallbackQuery,
) -> None:
    await _safe_edit(
        callback.message,
        "⚠️ <b>Скинути налаштування?</b>\n\n"
        "Це видалить всі налаштування, послуги та розклад.\n"
        "Записи клієнтів НЕ будуть видалені.\n\n"
        "Ви впевнені?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Так, скинути", callback_data="ttts_reset_confirm")],
            [_back_to_settings_btn()],
        ]),
    )
    await callback.answer()


async def settings_reset_confirm(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    state: FSMContext,
) -> None:
    await session.execute(
        delete(BotConfig).where(BotConfig.bot_id == registered_bot_id)
    )
    await session.execute(
        delete(TattooService).where(TattooService.bot_id == registered_bot_id)
    )
    await session.execute(
        delete(ApptSchedule).where(ApptSchedule.bot_id == registered_bot_id)
    )
    await session.commit()
    await state.clear()
    await callback.answer("🔄 Налаштування скинуто!")

    from app.bot.handlers.niche.tattoo.wizard import start_wizard
    await callback.message.answer("🔄 Починаємо налаштування заново...")
    await start_wizard(callback.message, state, session, registered_bot_id)


# ── Registration ───────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.callback_query.register(settings_menu_cb, F.data == "ttts_menu")

    # Profile
    dp.callback_query.register(settings_profile,      F.data == "ttts_prof")
    dp.callback_query.register(settings_profile_edit, F.data.startswith("ttts_prof_edit:"))
    dp.message.register(settings_profile_save, TattooSettingsFSM.s_name,  F.text)
    dp.message.register(settings_profile_save, TattooSettingsFSM.s_bio,   F.text)
    dp.message.register(settings_profile_save, TattooSettingsFSM.s_city,  F.text)

    # Schedule
    dp.callback_query.register(settings_schedule,         F.data == "ttts_schedule")
    dp.callback_query.register(settings_sched_edit,       F.data == "ttts_sched_edit")
    dp.callback_query.register(settings_sched_day_toggle, F.data.startswith("ttts_sched_day_tog:"))
    dp.callback_query.register(settings_sched_days_done,  F.data == "ttts_sched_days_done")
    dp.callback_query.register(settings_sched_start,      F.data.startswith("ttts_sched_start:"))
    dp.callback_query.register(settings_sched_end,        F.data.startswith("ttts_sched_end:"))
    dp.callback_query.register(settings_sched_duration,   F.data.startswith("ttts_sched_dur:"))
    dp.callback_query.register(settings_sched_buffer,     F.data.startswith("ttts_sched_buf:"))

    # Services
    dp.callback_query.register(settings_services,      F.data == "ttts_services")
    dp.callback_query.register(settings_svc_add,       F.data == "ttts_svc_add")
    dp.callback_query.register(settings_svc_edit,      F.data.startswith("ttts_svc_edit:"))
    dp.callback_query.register(settings_svc_toggle,    F.data.startswith("ttts_svc_toggle:"))
    dp.callback_query.register(settings_svc_delete,    F.data.startswith("ttts_svc_del:"))
    dp.callback_query.register(settings_svc_desc_skip, F.data == "ttts_svc_desc_skip")
    dp.message.register(settings_svc_name_input,  TattooSettingsFSM.s_svc_name,  F.text)
    dp.message.register(settings_svc_price_input, TattooSettingsFSM.s_svc_price, F.text)
    dp.message.register(settings_svc_desc_input,  TattooSettingsFSM.s_svc_desc,  F.text)

    # Styles
    dp.callback_query.register(settings_styles,       F.data == "ttts_styles")
    dp.callback_query.register(settings_style_toggle, F.data.startswith("ttts_style_tog:"))
    dp.callback_query.register(settings_styles_save,  F.data == "ttts_styles_save")

    # Deposit
    dp.callback_query.register(settings_deposit,    F.data == "ttts_deposit")
    dp.callback_query.register(settings_dep_on,     F.data == "ttts_dep_on")
    dp.callback_query.register(settings_dep_off,    F.data == "ttts_dep_off")
    dp.callback_query.register(settings_dep_edit,   F.data.startswith("ttts_dep_edit:"))
    dp.message.register(settings_dep_save, TattooSettingsFSM.s_dep_amount,  F.text)
    dp.message.register(settings_dep_save, TattooSettingsFSM.s_dep_card,    F.text)
    dp.message.register(settings_dep_save, TattooSettingsFSM.s_dep_purpose, F.text)

    # Questionnaire
    dp.callback_query.register(settings_quest,        F.data == "ttts_quest")
    dp.callback_query.register(settings_quest_toggle, F.data.startswith("ttts_quest_tog:"))
    dp.callback_query.register(settings_quest_save,   F.data == "ttts_quest_save")

    # Reminders
    dp.callback_query.register(settings_reminders,  F.data == "ttts_reminders")
    dp.callback_query.register(settings_rem_toggle, F.data.startswith("ttts_rem_tog:"))
    dp.callback_query.register(settings_rem_save,   F.data == "ttts_rem_save")

    # Messages
    dp.callback_query.register(settings_messages,  F.data == "ttts_messages")
    dp.callback_query.register(settings_msg_edit,  F.data.startswith("ttts_msg_edit:"))
    dp.callback_query.register(settings_msg_reset, F.data.startswith("ttts_msg_reset:"))
    dp.message.register(settings_msg_save, TattooSettingsFSM.s_msg_edit, F.text)

    # Restrictions
    dp.callback_query.register(settings_restrict,           F.data == "ttts_restrict")
    dp.callback_query.register(settings_age_on,             F.data == "ttts_age_on")
    dp.callback_query.register(settings_age_off,            F.data == "ttts_age_off")
    dp.callback_query.register(settings_age_edit,           F.data == "ttts_age_edit")
    dp.callback_query.register(settings_cancel_edit,        F.data == "ttts_cancel_edit")
    dp.message.register(settings_age_text_input,    TattooSettingsFSM.s_age_text,     F.text)
    dp.message.register(settings_cancel_hours_input, TattooSettingsFSM.s_cancel_hours, F.text)

    # Reset
    dp.callback_query.register(settings_reset,         F.data == "ttts_reset")
    dp.callback_query.register(settings_reset_confirm, F.data == "ttts_reset_confirm")
