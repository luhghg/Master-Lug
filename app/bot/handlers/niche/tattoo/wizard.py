"""Onboarding wizard for TATTOO niche — walks master through initial setup."""
import logging

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import ApptSchedule
from app.models.tattoo import TattooService
from app.services.config_service import get_cfg, get_json, set_cfg, set_json

logger = logging.getLogger(__name__)

# ── Config key constants ───────────────────────────────────────────────────────

TTT_ONBOARDING_DONE  = "ttt_onboarding_completed"
TTT_MASTER_NAME      = "ttt_master_name"
TTT_MASTER_BIO       = "ttt_master_bio"
TTT_MASTER_CITY      = "ttt_master_city"
TTT_STYLES           = "ttt_styles"
TTT_DEPOSIT_ENABLED  = "ttt_deposit_enabled"
TTT_DEPOSIT_AMOUNT   = "ttt_deposit_amount"
TTT_CARD_NUMBER      = "ttt_card_number"
TTT_DEPOSIT_PURPOSE  = "ttt_deposit_purpose"
TTT_QUESTIONNAIRE    = "ttt_questionnaire"
TTT_REMINDERS        = "ttt_reminders"
TTT_MSG_WELCOME      = "ttt_welcome"
TTT_MSG_CONFIRM      = "ttt_msg_confirm"
TTT_MSG_REMINDER_TPL = "ttt_msg_reminder"
TTT_MSG_AFTERCARE    = "ttt_msg_aftercare"
TTT_MSG_REVIEW_TPL   = "ttt_msg_review"
TTT_MSG_DEPOSIT      = "ttt_msg_deposit"
TTT_MIN_AGE_ENABLED  = "ttt_min_age_enabled"
TTT_MIN_AGE_TEXT     = "ttt_min_age_text"
TTT_CANCEL_HOURS     = "ttt_cancel_hours"
TTT_MASTER_SOCIAL    = "ttt_social"
TTT_SCHEDULE_MODE    = "ttt_schedule_mode"   # "fixed" | "flexible"

_TMPL = {
    TTT_MSG_WELCOME: "👋 <b>Ласкаво просимо!</b>\n\nОберіть що вас цікавить:",
    TTT_MSG_CONFIRM: "✅ <b>Ваш запис підтверджено!</b>\n\n📅 {date} о {time}\n\nЧекаємо вас! Якщо щось зміниться — напишіть заздалегідь.",
    TTT_MSG_REMINDER_TPL: "⏰ <b>Нагадування!</b>\n\nПривіт! Нагадуємо про ваш запис:\n📅 {date} о {time}\n\nБудьте вчасно 😊",
    TTT_MSG_AFTERCARE: "🌿 <b>Догляд після сеансу</b>\n\n• Перші 2–4 год: плівка на місці\n• Після зняття: промий теплою водою\n• Пантенол або крем 3-4 рази на день 2 тижні\n• Уникай сонця і хлорованої води 3 тижні\n\nПитання — пиши 🙏",
    TTT_MSG_REVIEW_TPL: "⭐ <b>Як враження від сеансу?</b>\n\nБудемо вдячні за чесний відгук!",
    TTT_MSG_DEPOSIT: "💳 <b>Для підтвердження запису необхідний депозит</b>\n\n💰 Сума: {amount} грн\n💳 Картка: {card}\n📝 Призначення: {purpose}\n\nНадішліть скріншот оплати після переказу.",
}

_STYLE_OPTIONS = [
    "Реалізм", "Blackwork", "Акварель", "Дотворк",
    "Геометрія", "Олд-скул", "Неотрадиція", "Леттерінг",
    "Японський", "Трайбл",
]

_COMMON_CITIES = ["Київ", "Харків", "Одеса", "Дніпро", "Запоріжжя", "Львів", "Інше"]

_DAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

_QUEST_FIELDS = [
    ("zone",      "Зона і розмір"),
    ("reference", "Референс (фото)"),
    ("allergy",   "Алергія"),
    ("overlap",   "Перекриття"),
]

_REMINDER_FIELDS = [
    ("7d",     "За 7 днів"),
    ("24h",    "За 24 години"),
    ("2h",     "За 2 години"),
    ("review", "Відгук після сеансу"),
]

_MSG_LABELS = [
    (TTT_MSG_WELCOME,      "Привітання"),
    (TTT_MSG_CONFIRM,      "Підтвердження запису"),
    (TTT_MSG_REMINDER_TPL, "Нагадування"),
    (TTT_MSG_AFTERCARE,    "Догляд"),
    (TTT_MSG_REVIEW_TPL,   "Відгук"),
    (TTT_MSG_DEPOSIT,      "Повідомлення про депозит"),
]


# ── FSM ────────────────────────────────────────────────────────────────────────

class TattooWizardFSM(StatesGroup):
    w_name             = State()
    w_bio              = State()
    w_city             = State()
    w_styles           = State()
    w_services         = State()
    w_svc_name         = State()
    w_svc_price        = State()
    w_svc_desc         = State()
    w_sched_mode_pick  = State()   # Fixed vs Flexible
    w_sched_days       = State()
    w_sched_start      = State()   # text: HH:MM
    w_sched_end        = State()   # text: HH:MM
    w_sched_duration   = State()   # buttons or custom
    w_sched_dur_custom = State()   # text: minutes
    w_sched_buffer     = State()   # buttons or custom
    w_sched_buf_custom = State()   # text: minutes
    w_deposit          = State()
    w_deposit_amount   = State()
    w_deposit_card     = State()
    w_deposit_purpose  = State()
    w_questionnaire    = State()
    w_reminders        = State()
    w_messages         = State()
    w_msg_edit         = State()


# ── Keyboard builders ──────────────────────────────────────────────────────────

def _interrupt_btn() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="❌ Перервати", callback_data="tttw_interrupt")


def _back_btn(step: int) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="◀️ Назад", callback_data=f"tttw_back:{step}")


def _step_header(step: int, title: str) -> str:
    return f"🔧 <b>Налаштування бота — Крок {step} з 9</b>\n\n{title}"


def _styles_kb(selected: list[str]) -> types.InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(_STYLE_OPTIONS), 2):
        row = []
        for opt in _STYLE_OPTIONS[i:i+2]:
            mark = "✅" if opt in selected else "◻️"
            row.append(types.InlineKeyboardButton(
                text=f"{mark} {opt}", callback_data=f"tttw_style_tog:{opt}"
            ))
        rows.append(row)
    rows.append([types.InlineKeyboardButton(
        text="➕ Свій стиль текстом", callback_data="tttw_style_custom"
    )])
    rows.append([types.InlineKeyboardButton(
        text=f"✅ Готово (вибрано: {len(selected)})", callback_data="tttw_styles_done"
    )])
    rows.append([_back_btn(1), _interrupt_btn()])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _days_kb(selected: list[int]) -> types.InlineKeyboardMarkup:
    row1 = []
    for i in range(7):
        mark = "✅" if i in selected else "◻️"
        row1.append(types.InlineKeyboardButton(
            text=f"{mark}{_DAYS_SHORT[i]}", callback_data=f"tttw_day_tog:{i}"
        ))
    return types.InlineKeyboardMarkup(inline_keyboard=[
        row1[:4],
        row1[4:],
        [types.InlineKeyboardButton(text="✅ Підтвердити дні", callback_data="tttw_days_done")],
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="tttw_sched_mode"), _interrupt_btn()],
    ])


def _parse_time(text: str) -> str | None:
    """Parse 'H:MM' or 'HH:MM'. Returns 'HH:MM' or None if invalid."""
    text = text.strip().replace(".", ":")
    if ":" not in text:
        return None
    h_str, m_str = text.split(":", 1)
    try:
        h, m = int(h_str), int(m_str)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def _parse_minutes(text: str, min_val: int = 1, max_val: int = 720) -> int | None:
    try:
        m = int(text.strip())
    except (ValueError, TypeError):
        return None
    return m if min_val <= m <= max_val else None


def _duration_kb() -> types.InlineKeyboardMarkup:
    quick = [("30 хв", 30), ("1 год", 60), ("1.5 год", 90),
             ("2 год", 120), ("4 год", 240), ("8 год", 480)]
    rows = [
        [types.InlineKeyboardButton(text=lbl, callback_data=f"tttw_sched_dur:{m}") for lbl, m in quick[:3]],
        [types.InlineKeyboardButton(text=lbl, callback_data=f"tttw_sched_dur:{m}") for lbl, m in quick[3:]],
        [types.InlineKeyboardButton(text="✏️ Вказати своє (хвилини)", callback_data="tttw_sched_dur_custom")],
        [_sched_sub_back_btn("end"), _interrupt_btn()],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _buffer_kb() -> types.InlineKeyboardMarkup:
    quick = [("0 хв", 0), ("15 хв", 15), ("30 хв", 30), ("45 хв", 45), ("60 хв", 60)]
    rows = [
        [types.InlineKeyboardButton(text=lbl, callback_data=f"tttw_sched_buf:{m}") for lbl, m in quick[:3]],
        [types.InlineKeyboardButton(text=lbl, callback_data=f"tttw_sched_buf:{m}") for lbl, m in quick[3:]],
        [types.InlineKeyboardButton(text="✏️ Вказати своє (хвилини)", callback_data="tttw_sched_buf_custom")],
        [_sched_sub_back_btn("dur"), _interrupt_btn()],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _sched_nav_kb(back_step: int = 3) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[_back_btn(back_step), _interrupt_btn()]])


def _sched_sub_back_btn(target: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="◀️ Назад", callback_data=f"tttw_sched_back:{target}")


def _sched_sub_back_kb(target: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[_sched_sub_back_btn(target), _interrupt_btn()]])


def _quest_kb(state_dict: dict) -> types.InlineKeyboardMarkup:
    rows = []
    for key, label in _QUEST_FIELDS:
        on = state_dict.get(key, True)
        mark = "✅" if on else "◻️"
        rows.append([types.InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"tttw_quest_tog:{key}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти і далі", callback_data="tttw_quest_done")])
    rows.append([_back_btn(6), _interrupt_btn()])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _reminders_kb(state_dict: dict) -> types.InlineKeyboardMarkup:
    rows = []
    for key, label in _REMINDER_FIELDS:
        on = state_dict.get(key, True)
        mark = "✅" if on else "◻️"
        rows.append([types.InlineKeyboardButton(
            text=f"{mark} {label}", callback_data=f"tttw_rem_tog:{key}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Зберегти і далі", callback_data="tttw_rem_done")])
    rows.append([_back_btn(7), _interrupt_btn()])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _messages_kb() -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(
        text="✅ Залишити всі стандартні", callback_data="tttw_msg_keep_all"
    )]]
    for key, label in _MSG_LABELS:
        rows.append([types.InlineKeyboardButton(
            text=f"✏️ {label}", callback_data=f"tttw_msg_edit:{key}"
        )])
    rows.append([types.InlineKeyboardButton(text="✅ Готово, далі", callback_data="tttw_msg_done")])
    rows.append([_back_btn(8), _interrupt_btn()])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# ── Entry point ────────────────────────────────────────────────────────────────

async def start_wizard(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot_id: int,
) -> None:
    data = await state.get_data()
    if data.get("wizard_interrupted"):
        await message.answer(
            "🔧 <b>Ви раніше перервали налаштування.</b>\n\nПродовжити з місця зупинки або почати знову?",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="▶️ Продовжити", callback_data="tttw_resume")],
                [types.InlineKeyboardButton(text="🔄 Почати знову", callback_data="tttw_restart")],
            ]),
        )
        return
    await _step1_start(message, state)


async def _step1_start(message: types.Message, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_name)
    await message.answer(
        _step_header(1, "👤 <b>Профіль майстра</b>\n\nВведіть ваше ім'я або назву студії (до 64 символів):"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_interrupt_btn()]]),
    )


# ── Step 1: Profile ────────────────────────────────────────────────────────────

async def w_name_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    text = message.text.strip() if message.text else ""
    if not text or len(text) > 64:
        await message.answer("Будь ласка, введіть ім'я до 64 символів:")
        return
    await set_cfg(session, registered_bot_id, TTT_MASTER_NAME, text)
    await state.update_data(w_name=text)
    await state.set_state(TattooWizardFSM.w_bio)
    await message.answer(
        _step_header(1, "👤 <b>Профіль майстра</b>\n\nРозкажіть про себе клієнтам (до 300 символів):"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_interrupt_btn()]]),
    )


async def w_bio_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    text = message.text.strip() if message.text else ""
    if not text or len(text) > 300:
        await message.answer("Будь ласка, введіть текст до 300 символів:")
        return
    await set_cfg(session, registered_bot_id, TTT_MASTER_BIO, text)
    await state.update_data(w_bio=text)
    await state.set_state(TattooWizardFSM.w_city)
    city_rows = [[types.InlineKeyboardButton(text=c, callback_data=f"tttw_city:{c}")] for c in _COMMON_CITIES]
    city_rows.append([_interrupt_btn()])
    await message.answer(
        _step_header(1, "👤 <b>Профіль майстра</b>\n\nМісто роботи (оберіть або введіть текстом):"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=city_rows),
    )


async def w_city_btn(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    city = callback.data.split(":", 1)[1]
    if city == "Інше":
        await callback.answer()
        await callback.message.edit_text(
            _step_header(1, "👤 <b>Профіль майстра</b>\n\nВведіть своє місто:"),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_interrupt_btn()]]),
        )
        return
    await _save_city_and_goto_step2(callback.message, state, session, registered_bot_id, city)
    await callback.answer()


async def w_city_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    city = message.text.strip() if message.text else ""
    if not city:
        await message.answer("Введіть назву міста:")
        return
    await _save_city_and_goto_step2(message, state, session, registered_bot_id, city)


async def _save_city_and_goto_step2(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot_id: int,
    city: str,
) -> None:
    await set_cfg(session, bot_id, TTT_MASTER_CITY, city)
    await state.update_data(w_city=city)
    data = await state.get_data()
    selected = data.get("w_styles", [])
    await state.set_state(TattooWizardFSM.w_styles)
    try:
        await message.edit_text(
            _step_header(2, "🎨 <b>Стилі татуювання</b>\n\nОберіть стилі в яких ви працюєте (мінімум 1):"),
            reply_markup=_styles_kb(selected),
        )
    except Exception:
        await message.answer(
            _step_header(2, "🎨 <b>Стилі татуювання</b>\n\nОберіть стилі в яких ви працюєте (мінімум 1):"),
            reply_markup=_styles_kb(selected),
        )


# ── Step 2: Styles ─────────────────────────────────────────────────────────────

async def w_style_toggle(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    opt = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = list(data.get("w_styles", []))
    if opt in selected:
        selected.remove(opt)
    else:
        selected.append(opt)
    await state.update_data(w_styles=selected)
    try:
        await callback.message.edit_reply_markup(reply_markup=_styles_kb(selected))
    except Exception:
        pass
    await callback.answer()


async def w_style_custom(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await callback.message.answer(
        "Введіть назву свого стилю текстом:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_interrupt_btn()]]),
    )
    await state.update_data(w_adding_custom_style=True)


async def w_style_custom_input(
    message: types.Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    if not data.get("w_adding_custom_style"):
        return
    custom = message.text.strip() if message.text else ""
    if not custom:
        await message.answer("Введіть назву стилю:")
        return
    selected = list(data.get("w_styles", []))
    if custom not in selected:
        selected.append(custom)
    await state.update_data(w_styles=selected, w_adding_custom_style=False)
    await message.answer(
        _step_header(2, "🎨 <b>Стилі татуювання</b>\n\nОберіть стилі в яких ви працюєте:"),
        reply_markup=_styles_kb(selected),
    )


async def w_styles_done(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    selected = data.get("w_styles", [])
    if not selected:
        await callback.answer("Оберіть хоча б 1 стиль!", show_alert=True)
        return
    await set_json(session, registered_bot_id, TTT_STYLES, selected)
    await state.set_state(TattooWizardFSM.w_services)
    await _show_services_step(callback.message, state, edit=True)
    await callback.answer()


# ── Step 3: Services ───────────────────────────────────────────────────────────

async def _show_services_step(
    message: types.Message,
    state: FSMContext,
    edit: bool = False,
) -> None:
    data = await state.get_data()
    services_list = data.get("w_services_list", [])
    if services_list:
        lines = "\n".join(f"• {s['name']} — {s['price']} грн" for s in services_list)
        text = _step_header(3, f"💼 <b>Послуги та ціни</b>\n\n{lines}")
    else:
        text = _step_header(3, "💼 <b>Послуги та ціни</b>\n\nПоки порожньо. Додайте хоча б одну послугу.")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ Додати послугу", callback_data="tttw_svc_add")],
        [types.InlineKeyboardButton(
            text="✅ Готово, далі" if services_list else "⏩ Пропустити",
            callback_data="tttw_svc_done",
        )],
        [_back_btn(2), _interrupt_btn()],
    ])
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


async def w_svc_add(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_svc_name)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "Введіть назву послуги:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="◀️ Назад", callback_data="tttw_svc_back")],
            ]),
        )
    except Exception:
        await callback.message.answer(
            "Введіть назву послуги:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="◀️ Назад", callback_data="tttw_svc_back")],
            ]),
        )


async def w_svc_name_input(message: types.Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Введіть назву послуги:")
        return
    if len(name) > 100:
        await message.answer("⚠️ Назва занадто довга (максимум 100 символів). Скоротіть і надішліть ще раз:")
        return
    await state.update_data(w_svc_tmp_name=name)
    await state.set_state(TattooWizardFSM.w_svc_price)
    await message.answer("Ціна послуги (грн, тільки число або діапазон «1000-3000»):")


async def w_svc_price_input(message: types.Message, state: FSMContext) -> None:
    price = message.text.strip() if message.text else ""
    if not price:
        await message.answer("Введіть ціну:")
        return
    if len(price) > 50:
        await message.answer("⚠️ Ціна занадто довга (максимум 50 символів). Введіть коротше:")
        return
    await state.update_data(w_svc_tmp_price=price)
    await state.set_state(TattooWizardFSM.w_svc_desc)
    await message.answer(
        "Опис послуги (необов'язково, введіть «-» щоб пропустити):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⏩ Пропустити", callback_data="tttw_svc_desc_skip")],
        ]),
    )


async def w_svc_desc_skip(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _save_service_and_return(callback.message, state, desc=None, edit=True)
    await callback.answer()


async def w_svc_desc_input(message: types.Message, state: FSMContext) -> None:
    desc = message.text.strip() if message.text else ""
    if desc == "-":
        desc = None
    await _save_service_and_return(message, state, desc=desc, edit=False)


async def _save_service_and_return(
    message: types.Message,
    state: FSMContext,
    desc: str | None,
    edit: bool,
) -> None:
    data = await state.get_data()
    svc_list = list(data.get("w_services_list", []))
    svc_list.append({
        "name": data.get("w_svc_tmp_name", ""),
        "price": data.get("w_svc_tmp_price", ""),
        "desc": desc,
    })
    await state.update_data(
        w_services_list=svc_list,
        w_svc_tmp_name=None,
        w_svc_tmp_price=None,
    )
    await state.set_state(TattooWizardFSM.w_services)
    await _show_services_step(message, state, edit=edit)


async def w_svc_done(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await callback.answer()
    data = await state.get_data()
    svc_list = data.get("w_services_list", [])
    for i, svc in enumerate(svc_list):
        session.add(TattooService(
            bot_id=registered_bot_id,
            name=svc["name"],
            price=svc["price"],
            description=svc.get("desc"),
            position=i,
        ))
    if svc_list:
        await session.commit()
    await state.set_state(TattooWizardFSM.w_sched_mode_pick)
    await _show_sched_mode(callback.message, state, edit=True)


async def w_svc_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_services)
    await _show_services_step(callback.message, state, edit=True)
    await callback.answer()


# ── Step 4: Schedule ───────────────────────────────────────────────────────────

def _sched_mode_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="📅 Фіксований графік",
            callback_data="tttw_sched_mode_fixed",
        )],
        [types.InlineKeyboardButton(
            text="🔄 Гнучкий графік",
            callback_data="tttw_sched_mode_flex",
        )],
        [_back_btn(3), _interrupt_btn()],
    ])


async def _show_sched_mode(
    message: types.Message,
    state: FSMContext,
    edit: bool = False,
) -> None:
    text = _step_header(
        4,
        "🗓 <b>Як у вас влаштований графік роботи?</b>\n\n"
        "<b>📅 Фіксований графік</b> — працюю за розкладом (конкретні дні і "
        "години, система автоматично генерує доступні слоти для клієнтів).\n\n"
        "<b>🔄 Гнучкий графік</b> — без фіксованого розкладу, сам додаватиму "
        "вільний час вручну через панель майстра.",
    )
    if edit:
        try:
            await message.edit_text(text, reply_markup=_sched_mode_kb())
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=_sched_mode_kb())


async def w_sched_mode(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_sched_mode_pick)
    await _show_sched_mode(callback.message, state, edit=True)
    await callback.answer()


async def w_sched_mode_fixed(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_sched_days)
    await _show_sched_days(callback.message, state, edit=True)
    await callback.answer()


async def w_sched_mode_flexible(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, TTT_SCHEDULE_MODE, "flexible")
    await state.set_state(TattooWizardFSM.w_deposit)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "✅ <b>Зрозуміло!</b>\n\n"
            "Ви зможете додавати вільний час вручну через:\n"
            "<b>Панель майстра → 📅 Розклад → Додати слот</b>\n\n"
            "Клієнти бачитимуть тільки ті слоти, які ви самі додасте.",
            reply_markup=None,
        )
    except Exception:
        pass
    await callback.message.answer(
        _step_header(5, "💳 <b>Депозит</b>\n\nЧи хочете отримувати депозит для підтвердження запису?"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Увімкнути депозит", callback_data="tttw_dep_yes")],
            [types.InlineKeyboardButton(text="⏩ Пропустити (без депозиту)", callback_data="tttw_dep_no")],
            [_back_btn(4), _interrupt_btn()],
        ]),
    )


async def _show_sched_days(
    message: types.Message,
    state: FSMContext,
    edit: bool = False,
) -> None:
    data = await state.get_data()
    selected = data.get("w_sched_days", [])
    text = _step_header(4, "🗓 <b>Робочий розклад</b>\n\nОберіть робочі дні:")
    kb = _days_kb(selected)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


async def w_day_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    dow = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected = list(data.get("w_sched_days", []))
    if dow in selected:
        selected.remove(dow)
    else:
        selected.append(dow)
    await state.update_data(w_sched_days=selected)
    try:
        await callback.message.edit_reply_markup(reply_markup=_days_kb(selected))
    except Exception:
        pass
    await callback.answer()


async def w_days_done(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("w_sched_days"):
        await callback.answer("Оберіть хоча б 1 день!", show_alert=True)
        return
    await state.set_state(TattooWizardFSM.w_sched_start)
    prompt = _step_header(4, "🗓 <b>Розклад</b>\n\nВведіть час <b>початку</b> роботи у форматі ГГ:ХХ\n(наприклад: <code>09:00</code> або <code>10:30</code>):")
    try:
        await callback.message.edit_text(prompt, reply_markup=_sched_nav_kb(back_step=4))
    except Exception:
        await callback.message.answer(prompt, reply_markup=_sched_nav_kb(back_step=4))
    await callback.answer()


async def w_sched_start_input(message: types.Message, state: FSMContext) -> None:
    t = _parse_time(message.text or "")
    if t is None:
        await message.answer(
            "❌ Невірний формат. Введіть час у форматі ГГ:ХХ, наприклад: <code>09:00</code>",
            reply_markup=_sched_nav_kb(back_step=4),
        )
        return
    await state.update_data(w_sched_start=t)
    await state.set_state(TattooWizardFSM.w_sched_end)
    await message.answer(
        _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ Початок: <b>{t}</b>\n\nВведіть час <b>закінчення</b> роботи (наприклад: <code>18:00</code>):"),
        reply_markup=_sched_sub_back_kb("start"),
    )


async def w_sched_end_input(message: types.Message, state: FSMContext) -> None:
    t = _parse_time(message.text or "")
    if t is None:
        await message.answer(
            "❌ Невірний формат. Введіть час у форматі ГГ:ХХ, наприклад: <code>18:00</code>",
            reply_markup=_sched_sub_back_kb("start"),
        )
        return
    data = await state.get_data()
    start = data.get("w_sched_start", "00:00")
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, t.split(":"))
    if eh * 60 + em <= sh * 60 + sm:
        await message.answer(
            f"❌ Час закінчення (<b>{t}</b>) має бути <b>пізнішим</b> за час початку (<b>{start}</b>).\n\nВведіть час закінчення ще раз:",
            reply_markup=_sched_sub_back_kb("start"),
        )
        return
    await state.update_data(w_sched_end=t)
    await state.set_state(TattooWizardFSM.w_sched_duration)
    await message.answer(
        _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ {start} – {t}\n\nТривалість одного сеансу:"),
        reply_markup=_duration_kb(),
    )


async def w_sched_duration(callback: types.CallbackQuery, state: FSMContext) -> None:
    mins = int(callback.data.split(":")[1])
    await state.update_data(w_sched_duration=mins)
    await state.set_state(TattooWizardFSM.w_sched_buffer)
    try:
        await callback.message.edit_text(
            _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ Тривалість: <b>{mins} хв</b>\n\nПауза між сеансами (буфер):"),
            reply_markup=_buffer_kb(),
        )
    except Exception:
        await callback.message.answer(
            _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ Тривалість: <b>{mins} хв</b>\n\nПауза між сеансами (буфер):"),
            reply_markup=_buffer_kb(),
        )
    await callback.answer()


async def w_sched_dur_custom_btn(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_sched_dur_custom)
    await callback.answer()
    try:
        await callback.message.edit_text(
            _step_header(4, "🗓 <b>Розклад</b>\n\nВведіть тривалість сеансу в хвилинах (15–720):"),
            reply_markup=_sched_sub_back_kb("dur"),
        )
    except Exception:
        await callback.message.answer(
            _step_header(4, "🗓 <b>Розклад</b>\n\nВведіть тривалість сеансу в хвилинах (15–720):"),
            reply_markup=_sched_sub_back_kb("dur"),
        )


async def w_sched_dur_custom_input(message: types.Message, state: FSMContext) -> None:
    mins = _parse_minutes(message.text or "", min_val=15, max_val=720)
    if mins is None:
        await message.answer(
            "❌ Введіть ціле число від 15 до 720 хвилин (наприклад: <code>90</code>):",
            reply_markup=_sched_sub_back_kb("dur"),
        )
        return
    await state.update_data(w_sched_duration=mins)
    await state.set_state(TattooWizardFSM.w_sched_buffer)
    await message.answer(
        _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ Тривалість: <b>{mins} хв</b>\n\nПауза між сеансами (буфер):"),
        reply_markup=_buffer_kb(),
    )


async def _do_save_schedule(
    state: FSMContext, session: AsyncSession, bot_id: int, buf: int
) -> bool:
    """Save schedule rows. Returns False if days list is empty (guard)."""
    data = await state.get_data()
    days = data.get("w_sched_days", [])
    if not days:
        return False
    start = data.get("w_sched_start", "10:00")
    end   = data.get("w_sched_end",   "20:00")
    dur   = data.get("w_sched_duration", 60)
    for dow in days:
        stmt = (
            pg_insert(ApptSchedule)
            .values(
                bot_id=bot_id,
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
    return True


async def w_sched_buffer(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await callback.answer()
    buf = int(callback.data.split(":")[1])
    saved = await _do_save_schedule(state, session, registered_bot_id, buf)
    if not saved:
        await state.set_state(TattooWizardFSM.w_sched_days)
        await _show_sched_days(callback.message, state, edit=True)
        return
    await state.set_state(TattooWizardFSM.w_deposit)
    try:
        await callback.message.edit_text(
            _step_header(5, "💳 <b>Депозит</b>\n\nЧи хочете отримувати депозит для підтвердження запису?"),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Увімкнути депозит", callback_data="tttw_dep_yes")],
                [types.InlineKeyboardButton(text="⏩ Пропустити (без депозиту)", callback_data="tttw_dep_no")],
                [_back_btn(4), _interrupt_btn()],
            ]),
        )
    except Exception:
        await callback.message.answer(
            _step_header(5, "💳 <b>Депозит</b>\n\nЧи хочете отримувати депозит для підтвердження запису?"),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Увімкнути депозит", callback_data="tttw_dep_yes")],
                [types.InlineKeyboardButton(text="⏩ Пропустити (без депозиту)", callback_data="tttw_dep_no")],
                [_back_btn(4), _interrupt_btn()],
            ]),
        )


async def w_sched_buf_custom_btn(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_sched_buf_custom)
    await callback.answer()
    try:
        await callback.message.edit_text(
            _step_header(4, "🗓 <b>Розклад</b>\n\nВведіть паузу між сеансами в хвилинах (0–120):"),
            reply_markup=_sched_sub_back_kb("buf"),
        )
    except Exception:
        await callback.message.answer(
            _step_header(4, "🗓 <b>Розклад</b>\n\nВведіть паузу між сеансами в хвилинах (0–120):"),
            reply_markup=_sched_sub_back_kb("buf"),
        )


async def w_sched_buf_custom_input(
    message: types.Message, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    buf = _parse_minutes(message.text or "", min_val=0, max_val=120)
    if buf is None:
        await message.answer(
            "❌ Введіть ціле число від 0 до 120 хвилин (наприклад: <code>0</code> або <code>30</code>):",
            reply_markup=_sched_sub_back_kb("buf"),
        )
        return
    saved = await _do_save_schedule(state, session, registered_bot_id, buf)
    if not saved:
        await message.answer("⚠️ Список днів втрачено — починайте крок розкладу знову.")
        await state.set_state(TattooWizardFSM.w_sched_days)
        await _show_sched_days(message, state, edit=False)
        return
    await state.set_state(TattooWizardFSM.w_deposit)
    dep_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Увімкнути депозит", callback_data="tttw_dep_yes")],
        [types.InlineKeyboardButton(text="⏩ Пропустити (без депозиту)", callback_data="tttw_dep_no")],
        [_back_btn(4), _interrupt_btn()],
    ])
    await message.answer(
        _step_header(5, "💳 <b>Депозит</b>\n\nЧи хочете отримувати депозит для підтвердження запису?"),
        reply_markup=dep_kb,
    )


# ── Step 5: Deposit ────────────────────────────────────────────────────────────

async def w_dep_yes(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(w_deposit_enabled=True)
    await state.set_state(TattooWizardFSM.w_deposit_amount)
    await callback.answer()
    try:
        await callback.message.edit_text(
            _step_header(5, "💳 <b>Депозит</b>\n\nСума депозиту (грн, тільки число):"),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_back_btn(5), _interrupt_btn()]]),
        )
    except Exception:
        await callback.message.answer(
            _step_header(5, "💳 <b>Депозит</b>\n\nСума депозиту (грн, тільки число):"),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_back_btn(5), _interrupt_btn()]]),
        )


async def w_dep_no(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, TTT_DEPOSIT_ENABLED, "false")
    await state.update_data(w_deposit_enabled=False)
    await _goto_step6(callback.message, state, edit=True)
    await callback.answer()


async def w_deposit_amount_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    try:
        amount = int(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("Введіть тільки число (наприклад: 500):")
        return
    await set_cfg(session, registered_bot_id, TTT_DEPOSIT_AMOUNT, str(amount))
    await state.update_data(w_deposit_amount=amount)
    await state.set_state(TattooWizardFSM.w_deposit_card)
    await message.answer(
        _step_header(5, "💳 <b>Депозит</b>\n\nНомер картки для отримання:"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_back_btn(5), _interrupt_btn()]]),
    )


async def w_deposit_card_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    card = message.text.strip() if message.text else ""
    if not card:
        await message.answer("Введіть номер картки:")
        return
    await set_cfg(session, registered_bot_id, TTT_CARD_NUMBER, card)
    await state.update_data(w_deposit_card=card)
    await state.set_state(TattooWizardFSM.w_deposit_purpose)
    await message.answer(
        _step_header(5, "💳 <b>Депозит</b>\n\nПризначення платежу (наприклад: «Тату Оля депозит»):"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[_back_btn(5), _interrupt_btn()]]),
    )


async def w_deposit_purpose_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    purpose = message.text.strip() if message.text else ""
    if not purpose:
        await message.answer("Введіть призначення платежу:")
        return
    await set_cfg(session, registered_bot_id, TTT_DEPOSIT_PURPOSE, purpose)
    await set_cfg(session, registered_bot_id, TTT_DEPOSIT_ENABLED, "true")
    await state.update_data(w_deposit_purpose=purpose)
    await _goto_step6(message, state, edit=False)


async def _goto_step6(
    message: types.Message,
    state: FSMContext,
    edit: bool = False,
) -> None:
    data = await state.get_data()
    quest = data.get("w_quest", {"zone": True, "reference": True, "allergy": True, "overlap": True})
    await state.update_data(w_quest=quest)
    await state.set_state(TattooWizardFSM.w_questionnaire)
    text = _step_header(6, "📋 <b>Анкета клієнта</b>\n\nОберіть які питання задавати клієнту при записі:")
    kb = _quest_kb(quest)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


# ── Step 6: Questionnaire ──────────────────────────────────────────────────────

async def w_quest_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[1]
    data = await state.get_data()
    quest = dict(data.get("w_quest", {k: True for k, _ in _QUEST_FIELDS}))
    quest[key] = not quest.get(key, True)
    await state.update_data(w_quest=quest)
    try:
        await callback.message.edit_reply_markup(reply_markup=_quest_kb(quest))
    except Exception:
        pass
    await callback.answer()


async def w_quest_done(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    quest = data.get("w_quest", {k: True for k, _ in _QUEST_FIELDS})
    await set_json(session, registered_bot_id, TTT_QUESTIONNAIRE, quest)
    rems = data.get("w_reminders", {k: True for k, _ in _REMINDER_FIELDS})
    await state.update_data(w_reminders=rems)
    await state.set_state(TattooWizardFSM.w_reminders)
    try:
        await callback.message.edit_text(
            _step_header(7, "🔔 <b>Нагадування</b>\n\nОберіть коли надсилати нагадування клієнтам:"),
            reply_markup=_reminders_kb(rems),
        )
    except Exception:
        await callback.message.answer(
            _step_header(7, "🔔 <b>Нагадування</b>\n\nОберіть коли надсилати нагадування клієнтам:"),
            reply_markup=_reminders_kb(rems),
        )
    await callback.answer()


# ── Step 7: Reminders ──────────────────────────────────────────────────────────

async def w_rem_toggle(callback: types.CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[1]
    data = await state.get_data()
    rems = dict(data.get("w_reminders", {k: True for k, _ in _REMINDER_FIELDS}))
    rems[key] = not rems.get(key, True)
    await state.update_data(w_reminders=rems)
    try:
        await callback.message.edit_reply_markup(reply_markup=_reminders_kb(rems))
    except Exception:
        pass
    await callback.answer()


async def w_rem_done(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    rems = data.get("w_reminders", {k: True for k, _ in _REMINDER_FIELDS})
    await set_json(session, registered_bot_id, TTT_REMINDERS, rems)
    await state.set_state(TattooWizardFSM.w_messages)
    try:
        await callback.message.edit_text(
            _step_header(8, "💬 <b>Шаблони повідомлень</b>\n\nНалаштуйте або залиште стандартні тексти:"),
            reply_markup=_messages_kb(),
        )
    except Exception:
        await callback.message.answer(
            _step_header(8, "💬 <b>Шаблони повідомлень</b>\n\nНалаштуйте або залиште стандартні тексти:"),
            reply_markup=_messages_kb(),
        )
    await callback.answer()


# ── Step 8: Messages ───────────────────────────────────────────────────────────

async def w_msg_keep_all(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await _wizard_complete(callback.message, state, session, registered_bot_id, edit=True)
    await callback.answer()


async def w_msg_done(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await _wizard_complete(callback.message, state, session, registered_bot_id, edit=True)
    await callback.answer()


async def w_msg_edit_start(
    callback: types.CallbackQuery,
    state: FSMContext,
) -> None:
    key = callback.data.split(":", 1)[1]
    label = next((lbl for k, lbl in _MSG_LABELS if k == key), key)
    current = _TMPL.get(key, "")
    await state.update_data(w_editing_msg_key=key)
    await state.set_state(TattooWizardFSM.w_msg_edit)
    await callback.answer()
    try:
        await callback.message.edit_text(
            f"✏️ <b>{label}</b>\n\nПоточний текст:\n<code>{current}</code>\n\nВведіть новий текст:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="tttw_msg_cancel_edit")],
            ]),
        )
    except Exception:
        await callback.message.answer(
            f"✏️ <b>{label}</b>\n\nПоточний текст:\n<code>{current}</code>\n\nВведіть новий текст:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="◀️ Скасувати", callback_data="tttw_msg_cancel_edit")],
            ]),
        )


async def w_msg_edit_input(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    key = data.get("w_editing_msg_key")
    text = message.text.strip() if message.text else ""
    if not text or not key:
        await message.answer("Введіть текст повідомлення:")
        return
    await set_cfg(session, registered_bot_id, key, text)
    await state.update_data(w_editing_msg_key=None)
    await state.set_state(TattooWizardFSM.w_messages)
    await message.answer(
        _step_header(8, "💬 <b>Шаблони повідомлень</b>\n\n✅ Збережено! Продовжуйте налаштування:"),
        reply_markup=_messages_kb(),
    )


async def w_msg_cancel_edit(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TattooWizardFSM.w_messages)
    await callback.answer()
    try:
        await callback.message.edit_text(
            _step_header(8, "💬 <b>Шаблони повідомлень</b>"),
            reply_markup=_messages_kb(),
        )
    except Exception:
        await callback.message.answer(
            _step_header(8, "💬 <b>Шаблони повідомлень</b>"),
            reply_markup=_messages_kb(),
        )


# ── Completion ─────────────────────────────────────────────────────────────────

async def _wizard_complete(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot_id: int,
    edit: bool = False,
) -> None:
    for key, default in _TMPL.items():
        existing = await get_cfg(session, bot_id, key)
        if existing is None:
            await set_cfg(session, bot_id, key, default)
    await set_cfg(session, bot_id, TTT_ONBOARDING_DONE, "true")
    await state.clear()

    data_summary = (
        "✅ <b>Налаштування завершено!</b>\n\n"
        "Ваш бот готовий до роботи. Клієнти можуть записуватись через меню.\n\n"
        "Ви завжди можете змінити налаштування через панель майстра."
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⚙️ Перейти в панель майстра", callback_data="tttm_admin:home")],
    ])
    if edit:
        try:
            await message.edit_text(data_summary, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(data_summary, reply_markup=kb)


# ── Interrupt / Resume / Restart ───────────────────────────────────────────────

async def w_interrupt(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(wizard_interrupted=True)
    current_state = await state.get_state()
    await state.update_data(wizard_last_state=current_state)
    await state.set_state(None)
    await callback.answer()
    try:
        await callback.message.edit_text(
            "⏸ Налаштування збережено. Продовжте пізніше через /start",
            reply_markup=None,
        )
    except Exception:
        await callback.message.answer("⏸ Налаштування збережено. Продовжте пізніше через /start")


async def w_resume(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    data = await state.get_data()
    last_state = data.get("wizard_last_state")
    await state.update_data(wizard_interrupted=False)
    await callback.answer()

    state_to_step = {
        TattooWizardFSM.w_name: 1,
        TattooWizardFSM.w_bio: 1,
        TattooWizardFSM.w_city: 1,
        TattooWizardFSM.w_styles: 2,
        TattooWizardFSM.w_services: 3,
        TattooWizardFSM.w_svc_name: 3,
        TattooWizardFSM.w_svc_price: 3,
        TattooWizardFSM.w_svc_desc: 3,
        TattooWizardFSM.w_sched_mode_pick: 4,
        TattooWizardFSM.w_sched_days: 4,
        TattooWizardFSM.w_sched_start: 4,
        TattooWizardFSM.w_sched_end: 4,
        TattooWizardFSM.w_sched_duration: 4,
        TattooWizardFSM.w_sched_buffer: 4,
        TattooWizardFSM.w_deposit: 5,
        TattooWizardFSM.w_deposit_amount: 5,
        TattooWizardFSM.w_deposit_card: 5,
        TattooWizardFSM.w_deposit_purpose: 5,
        TattooWizardFSM.w_questionnaire: 6,
        TattooWizardFSM.w_reminders: 7,
        TattooWizardFSM.w_messages: 8,
        TattooWizardFSM.w_msg_edit: 8,
    }

    step = 1
    for fsm_state, s in state_to_step.items():
        if last_state and last_state.endswith(str(fsm_state).split(":")[-1]):
            step = s
            break

    await _goto_step_number(callback.message, state, session, registered_bot_id, step, edit=True)


async def w_restart(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    await state.clear()
    await callback.answer()
    await _step1_start(callback.message, state)


async def _goto_step_number(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot_id: int,
    step: int,
    edit: bool = False,
) -> None:
    if step <= 1:
        await state.set_state(TattooWizardFSM.w_name)
        text = _step_header(1, "👤 <b>Профіль майстра</b>\n\nВведіть ваше ім'я або назву студії (до 64 символів):")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[_interrupt_btn()]])
        if edit:
            try:
                await message.edit_text(text, reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=kb)
    elif step == 2:
        data = await state.get_data()
        selected = data.get("w_styles", [])
        await state.set_state(TattooWizardFSM.w_styles)
        text = _step_header(2, "🎨 <b>Стилі татуювання</b>\n\nОберіть стилі:")
        if edit:
            try:
                await message.edit_text(text, reply_markup=_styles_kb(selected))
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=_styles_kb(selected))
    elif step == 3:
        await state.set_state(TattooWizardFSM.w_services)
        await _show_services_step(message, state, edit=edit)
    elif step == 4:
        await state.set_state(TattooWizardFSM.w_sched_mode_pick)
        await _show_sched_mode(message, state, edit=edit)
    elif step == 5:
        await state.set_state(TattooWizardFSM.w_deposit)
        text = _step_header(5, "💳 <b>Депозит</b>\n\nЧи хочете отримувати депозит?")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Увімкнути депозит", callback_data="tttw_dep_yes")],
            [types.InlineKeyboardButton(text="⏩ Пропустити", callback_data="tttw_dep_no")],
            [_back_btn(4), _interrupt_btn()],
        ])
        if edit:
            try:
                await message.edit_text(text, reply_markup=kb)
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=kb)
    elif step == 6:
        await _goto_step6(message, state, edit=edit)
    elif step == 7:
        data = await state.get_data()
        rems = data.get("w_reminders", {k: True for k, _ in _REMINDER_FIELDS})
        await state.set_state(TattooWizardFSM.w_reminders)
        text = _step_header(7, "🔔 <b>Нагадування</b>")
        if edit:
            try:
                await message.edit_text(text, reply_markup=_reminders_kb(rems))
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=_reminders_kb(rems))
    else:
        await state.set_state(TattooWizardFSM.w_messages)
        text = _step_header(8, "💬 <b>Шаблони повідомлень</b>")
        if edit:
            try:
                await message.edit_text(text, reply_markup=_messages_kb())
                return
            except Exception:
                pass
        await message.answer(text, reply_markup=_messages_kb())


# ── Back navigation ────────────────────────────────────────────────────────────

async def w_sched_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handles back navigation within schedule sub-steps (days→start→end→dur→buf)."""
    target = callback.data.split(":")[1]
    await callback.answer()
    data = await state.get_data()

    if target == "start":
        await state.set_state(TattooWizardFSM.w_sched_start)
        prompt = _step_header(4, "🗓 <b>Розклад</b>\n\nВведіть час <b>початку</b> роботи у форматі ГГ:ХХ\n(наприклад: <code>09:00</code> або <code>10:30</code>):")
        try:
            await callback.message.edit_text(prompt, reply_markup=_sched_nav_kb(back_step=4))
        except Exception:
            await callback.message.answer(prompt, reply_markup=_sched_nav_kb(back_step=4))

    elif target == "end":
        start = data.get("w_sched_start", "??:??")
        await state.set_state(TattooWizardFSM.w_sched_end)
        prompt = _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ Початок: <b>{start}</b>\n\nВведіть час <b>закінчення</b> роботи (наприклад: <code>18:00</code>):")
        try:
            await callback.message.edit_text(prompt, reply_markup=_sched_sub_back_kb("start"))
        except Exception:
            await callback.message.answer(prompt, reply_markup=_sched_sub_back_kb("start"))

    elif target == "dur":
        start = data.get("w_sched_start", "??:??")
        end = data.get("w_sched_end", "??:??")
        await state.set_state(TattooWizardFSM.w_sched_duration)
        text = _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ {start} – {end}\n\nТривалість одного сеансу:")
        try:
            await callback.message.edit_text(text, reply_markup=_duration_kb())
        except Exception:
            await callback.message.answer(text, reply_markup=_duration_kb())

    elif target == "buf":
        dur = data.get("w_sched_duration", 60)
        await state.set_state(TattooWizardFSM.w_sched_buffer)
        text = _step_header(4, f"🗓 <b>Розклад</b>\n\n✅ Тривалість: <b>{dur} хв</b>\n\nПауза між сеансами (буфер):")
        try:
            await callback.message.edit_text(text, reply_markup=_buffer_kb())
        except Exception:
            await callback.message.answer(text, reply_markup=_buffer_kb())


async def w_back(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    step = int(callback.data.split(":")[1])
    await callback.answer()
    await _goto_step_number(callback.message, state, session, registered_bot_id, step, edit=True)


# ── Registration ───────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    # Resume / restart
    dp.callback_query.register(w_resume,  F.data == "tttw_resume")
    dp.callback_query.register(w_restart, F.data == "tttw_restart")

    # Interrupt
    dp.callback_query.register(w_interrupt, F.data == "tttw_interrupt")

    # Back navigation
    dp.callback_query.register(w_back,       F.data.startswith("tttw_back:"))
    dp.callback_query.register(w_sched_back, F.data.startswith("tttw_sched_back:"))

    # Step 1 — profile
    dp.message.register(w_name_input,  TattooWizardFSM.w_name,  F.text)
    dp.message.register(w_bio_input,   TattooWizardFSM.w_bio,   F.text)
    dp.callback_query.register(w_city_btn, TattooWizardFSM.w_city, F.data.startswith("tttw_city:"))
    dp.message.register(w_city_input,  TattooWizardFSM.w_city,  F.text)

    # Step 2 — styles
    dp.callback_query.register(w_style_toggle,       F.data.startswith("tttw_style_tog:"))
    dp.callback_query.register(w_style_custom,       F.data == "tttw_style_custom")
    dp.callback_query.register(w_styles_done,        F.data == "tttw_styles_done")
    dp.message.register(w_style_custom_input, TattooWizardFSM.w_styles, F.text)

    # Step 3 — services
    dp.callback_query.register(w_svc_add,  F.data == "tttw_svc_add")
    dp.callback_query.register(w_svc_done, F.data == "tttw_svc_done")
    dp.callback_query.register(w_svc_back, F.data == "tttw_svc_back")
    dp.callback_query.register(w_svc_desc_skip, F.data == "tttw_svc_desc_skip")
    dp.message.register(w_svc_name_input,  TattooWizardFSM.w_svc_name,  F.text)
    dp.message.register(w_svc_price_input, TattooWizardFSM.w_svc_price, F.text)
    dp.message.register(w_svc_desc_input,  TattooWizardFSM.w_svc_desc,  F.text)

    # Step 4 — schedule mode pick
    dp.callback_query.register(w_sched_mode,         F.data == "tttw_sched_mode")
    dp.callback_query.register(w_sched_mode_fixed,   F.data == "tttw_sched_mode_fixed")
    dp.callback_query.register(w_sched_mode_flexible, F.data == "tttw_sched_mode_flex")

    # Step 4 — fixed schedule sub-steps
    dp.callback_query.register(w_day_toggle, F.data.startswith("tttw_day_tog:"))
    dp.callback_query.register(w_days_done,  F.data == "tttw_days_done")
    # start/end: text input
    dp.message.register(w_sched_start_input,      TattooWizardFSM.w_sched_start,      F.text)
    dp.message.register(w_sched_end_input,        TattooWizardFSM.w_sched_end,        F.text)
    # duration: quick buttons or custom text
    dp.callback_query.register(w_sched_duration,      F.data.startswith("tttw_sched_dur:"))
    dp.callback_query.register(w_sched_dur_custom_btn, F.data == "tttw_sched_dur_custom")
    dp.message.register(w_sched_dur_custom_input, TattooWizardFSM.w_sched_dur_custom, F.text)
    # buffer: quick buttons or custom text
    dp.callback_query.register(w_sched_buffer,        F.data.startswith("tttw_sched_buf:"))
    dp.callback_query.register(w_sched_buf_custom_btn, F.data == "tttw_sched_buf_custom")
    dp.message.register(w_sched_buf_custom_input, TattooWizardFSM.w_sched_buf_custom, F.text)

    # Step 5 — deposit
    dp.callback_query.register(w_dep_yes, F.data == "tttw_dep_yes")
    dp.callback_query.register(w_dep_no,  F.data == "tttw_dep_no")
    dp.message.register(w_deposit_amount_input,  TattooWizardFSM.w_deposit_amount,   F.text)
    dp.message.register(w_deposit_card_input,    TattooWizardFSM.w_deposit_card,     F.text)
    dp.message.register(w_deposit_purpose_input, TattooWizardFSM.w_deposit_purpose,  F.text)

    # Step 6 — questionnaire
    dp.callback_query.register(w_quest_toggle, F.data.startswith("tttw_quest_tog:"))
    dp.callback_query.register(w_quest_done,   F.data == "tttw_quest_done")

    # Step 7 — reminders
    dp.callback_query.register(w_rem_toggle, F.data.startswith("tttw_rem_tog:"))
    dp.callback_query.register(w_rem_done,   F.data == "tttw_rem_done")

    # Step 8 — messages
    dp.callback_query.register(w_msg_keep_all,    F.data == "tttw_msg_keep_all")
    dp.callback_query.register(w_msg_done,        F.data == "tttw_msg_done")
    dp.callback_query.register(w_msg_edit_start,  F.data.startswith("tttw_msg_edit:"))
    dp.callback_query.register(w_msg_cancel_edit, F.data == "tttw_msg_cancel_edit")
    dp.message.register(w_msg_edit_input, TattooWizardFSM.w_msg_edit, F.text)
