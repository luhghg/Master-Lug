import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application, ApplicationStatus
from app.models.blocked_user import BotBlockedUser
from app.models.job import Job, JobStatus, JobType
from app.models.tattoo import BotSubscription
from app.models.user import User
from app.services.config_service import get_cfg, set_cfg
from app.services.job_service import create_job, format_job_card, generate_deep_link

logger = logging.getLogger(__name__)

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


PAY_HINT = (
    "💰 Опишіть умови оплати:\n\n"
    "<i>Підказка: вкажіть суму та спосіб розрахунку.\n"
    "Наприклад:\n"
    "• 200 грн/год\n"
    "• 800 грн/день\n"
    "• 15 000 грн/міс\n"
    "• 500 грн + чайові\n"
    "• Домовимось на місці</i>"
)


# ── FSM: Create job ───────────────────────────────────────────────────────────

class CreateJobFSM(StatesGroup):
    city            = State()
    job_type        = State()
    description     = State()
    pay_description = State()
    workers_needed  = State()
    location        = State()
    scheduled_time  = State()
    confirm         = State()


# ── FSM: Edit job ─────────────────────────────────────────────────────────────

class EditJobFSM(StatesGroup):
    select_field = State()
    waiting_value = State()


# ── FSM: Broadcast to accepted workers ───────────────────────────────────────

class BroadcastFSM(StatesGroup):
    waiting_message = State()


# ── FSM: Accept with details ──────────────────────────────────────────────────

class AcceptDetailsFSM(StatesGroup):
    waiting_details = State()


# ── Employer panel ────────────────────────────────────────────────────────────

async def employer_panel(callback: types.CallbackQuery) -> None:
    await _safe_edit(callback.message, "👔 <b>Панель роботодавця</b>", reply_markup=_employer_keyboard())
    await callback.answer()


async def cmd_menu_employer(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("👔 <b>Панель роботодавця</b>", reply_markup=_employer_keyboard())


def _employer_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="➕ Нова вакансія",      callback_data="role:employer")],
            [types.InlineKeyboardButton(text="📋 Активні вакансії",  callback_data="employer:my_jobs"),
             types.InlineKeyboardButton(text="📁 Архів",             callback_data="employer:archive")],
            [types.InlineKeyboardButton(text="👷 Мої працівники",    callback_data="employer:active_workers"),
             types.InlineKeyboardButton(text="🚫 Заблоковані",       callback_data="employer:blocked")],
            [types.InlineKeyboardButton(text="⚙️ Налаштування",      callback_data="employer:settings")],
        ]
    )


# ── Create job FSM ────────────────────────────────────────────────────────────

async def start_create_job(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _safe_edit(callback.message, "📍 В якому місті потрібен працівник?")
    await state.set_state(CreateJobFSM.city)
    await callback.answer()


async def got_city(message: types.Message, state: FSMContext) -> None:
    await state.update_data(city=message.text.strip())
    await message.answer(
        "📌 Оберіть тип оголошення:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[
                types.InlineKeyboardButton(text="💵 Разова робота",   callback_data="jtype:ONETIME"),
                types.InlineKeyboardButton(text="📅 Постійна робота", callback_data="jtype:PERMANENT"),
            ]]
        ),
    )
    await state.set_state(CreateJobFSM.job_type)


async def got_job_type(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.update_data(job_type=callback.data.split(":", 1)[1])
    await callback.message.answer("📝 Опишіть завдання:")
    await state.set_state(CreateJobFSM.description)
    await callback.answer()


async def got_description(message: types.Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip())
    await message.answer(PAY_HINT)
    await state.set_state(CreateJobFSM.pay_description)


async def got_pay_description(message: types.Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("❌ Занадто коротко. Опишіть умови оплати детальніше.")
        return
    await state.update_data(pay_description=text)
    await message.answer("👥 Скільки працівників потрібно? (1–50):")
    await state.set_state(CreateJobFSM.workers_needed)


async def got_workers_needed(message: types.Message, state: FSMContext) -> None:
    try:
        count = int(message.text.strip())
        if count < 1 or count > 50:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введіть число від 1 до 50")
        return
    await state.update_data(workers_needed=count)
    await message.answer("📌 Вкажіть адресу / місце роботи:")
    await state.set_state(CreateJobFSM.location)


async def got_location(message: types.Message, state: FSMContext) -> None:
    await state.update_data(location=message.text.strip())
    data = await state.get_data()
    if data["job_type"] == JobType.PERMANENT.value:
        await message.answer("📅 Коли можна приступити? (ДД.ММ.РРРР):")
    else:
        await message.answer("⏰ Дата та час роботи (ДД.ММ.РРРР ГГ:ХХ):")
    await state.set_state(CreateJobFSM.scheduled_time)


async def got_time(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    is_permanent = data["job_type"] == JobType.PERMANENT.value
    try:
        dt = datetime.strptime(
            message.text.strip(),
            "%d.%m.%Y" if is_permanent else "%d.%m.%Y %H:%M"
        )
    except ValueError:
        fmt = "25.12.2025" if is_permanent else "25.12.2025 09:00"
        await message.answer(f"❌ Невірний формат. Приклад: {fmt}")
        return

    if dt <= datetime.now():
        await message.answer("❌ Ця дата вже минула! Вкажіть майбутню дату.")
        return

    time_str = dt.strftime("%d.%m.%Y") if is_permanent else dt.strftime("%d.%m.%Y %H:%M")
    await state.update_data(scheduled_time=time_str)

    type_label = "📅 Постійна" if is_permanent else "💵 Разова"
    time_label = "Дата початку" if is_permanent else "Час роботи"

    await message.answer(
        "✅ <b>Підтвердіть вакансію:</b>\n\n"
        f"🏷 Тип: {type_label}\n"
        f"📍 Місто: {data['city']}\n"
        f"💰 Оплата: {data['pay_description']}\n"
        f"👥 Місць: {data['workers_needed']}\n"
        f"⏰ {time_label}: {time_str}\n"
        f"📌 Адреса: {data['location']}\n\n"
        f"📝 {data['description']}",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[
                types.InlineKeyboardButton(text="✅ Підтвердити", callback_data="job:confirm"),
                types.InlineKeyboardButton(text="❌ Скасувати",   callback_data="job:cancel"),
            ]]
        ),
    )
    await state.set_state(CreateJobFSM.confirm)


async def confirm_job(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    registered_bot_id: int,
    bot_username: str,
    bot: Bot,
) -> None:
    data = await state.get_data()
    is_permanent = data["job_type"] == JobType.PERMANENT.value
    fmt = "%d.%m.%Y" if is_permanent else "%d.%m.%Y %H:%M"

    job = await create_job(
        session,
        bot_id=registered_bot_id,
        employer_telegram_id=callback.from_user.id,
        job_type=JobType(data["job_type"]),
        city=data["city"],
        description=data["description"],
        pay_description=data["pay_description"],
        workers_needed=data["workers_needed"],
        location=data["location"],
        scheduled_time=datetime.strptime(data["scheduled_time"], fmt),
    )
    card = format_job_card(job, bot_username)
    await callback.message.answer(
        "🎉 <b>Вакансію опубліковано!</b>\n\nНижче — готове оголошення для поширення.",
    )
    await callback.message.answer(
        card,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text="📋 Копіювати оголошення",
                copy_text=types.CopyTextButton(text=card),
            )],
            [types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")],
        ]),
    )
    await state.clear()
    await callback.answer()

    subs_result = await session.execute(
        select(BotSubscription.telegram_id).where(
            BotSubscription.bot_id == registered_bot_id,
            BotSubscription.telegram_id != callback.from_user.id,
        )
    )
    subscriber_ids = [row[0] for row in subs_result.all()]
    if subscriber_ids:
        notif_text = "🔔 <b>Нова вакансія!</b>\n\nВідкрийте бот щоб переглянути та подати заявку /start"
        sent = 0
        for tid in subscriber_ids:
            try:
                await bot.send_message(chat_id=tid, text=notif_text)
                sent += 1
            except Exception:
                pass
        if sent:
            logger.info("Notified %d subscribers about new job %s", sent, job.id)


async def cancel_job(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(
        callback.message,
        "❌ Створення вакансії скасовано.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")
        ]]),
    )
    await callback.answer()


# ── Active jobs list ──────────────────────────────────────────────────────────

async def my_jobs(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    bot_username: str,
) -> None:
    result = await session.execute(
        select(Job)
        .where(Job.bot_id == registered_bot_id, Job.status == JobStatus.OPEN)
        .order_by(Job.created_at.desc())
        .limit(10)
    )
    jobs = list(result.scalars().all())

    if not jobs:
        await _safe_edit(
            callback.message,
            "У вас немає активних вакансій.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="➕ Створити вакансію", callback_data="role:employer")],
                [types.InlineKeyboardButton(text="◀️ Меню",              callback_data="employer:panel")],
            ]),
        )
        await callback.answer()
        return

    await callback.message.answer(f"📋 <b>Активні вакансії ({len(jobs)}):</b>")
    for job in jobs:
        accepted = await session.scalar(
            select(func.count()).where(
                Application.job_id == job.id,
                Application.status == ApplicationStatus.ACCEPTED,
            )
        ) or 0
        pending = await session.scalar(
            select(func.count()).where(
                Application.job_id == job.id,
                Application.status == ApplicationStatus.PENDING,
            )
        ) or 0
        type_label = "📅 Постійна" if job.job_type == JobType.PERMANENT else "💵 Разова"
        await callback.message.answer(
            f"{type_label} | <b>{job.city}</b>\n"
            f"📌 {job.location}\n"
            f"💰 {job.pay_description}\n"
            f"👥 Прийнято: {accepted}/{job.workers_needed} | Нових заявок: {pending}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [
                    types.InlineKeyboardButton(text="👥 Заявки",       callback_data=f"job:{job.id}:applicants"),
                    types.InlineKeyboardButton(text="✏️ Редагувати",   callback_data=f"job:{job.id}:edit"),
                ],
                [
                    types.InlineKeyboardButton(text="📢 Розсилка",     callback_data=f"job:{job.id}:broadcast"),
                    types.InlineKeyboardButton(text="📵 Деактивувати", callback_data=f"job:{job.id}:deactivate"),
                ],
            ]),
        )
    await callback.answer()


# ── Archive ───────────────────────────────────────────────────────────────────

async def archive(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
    bot_username: str,
) -> None:
    result = await session.execute(
        select(Job)
        .where(
            Job.bot_id == registered_bot_id,
            Job.status.in_([JobStatus.CANCELLED, JobStatus.COMPLETED, JobStatus.ASSIGNED]),
        )
        .order_by(Job.created_at.desc())
        .limit(15)
    )
    jobs = list(result.scalars().all())

    if not jobs:
        await _safe_edit(
            callback.message,
            "📁 Архів порожній.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")
            ]]),
        )
        await callback.answer()
        return

    await callback.message.answer(f"📁 <b>Архів вакансій ({len(jobs)}):</b>")
    for job in jobs:
        status_label = {
            JobStatus.CANCELLED: "❌ Скасована",
            JobStatus.COMPLETED: "✅ Завершена",
            JobStatus.ASSIGNED:  "🔒 Закрита (набрано)",
        }.get(job.status, job.status.value)

        time_str = job.scheduled_time.strftime("%d.%m.%Y")
        await callback.message.answer(
            f"{status_label}\n"
            f"<b>{job.city}</b> | {time_str}\n"
            f"📌 {job.location}\n"
            f"💰 {job.pay_description}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(
                    text="🔁 Перепостити",
                    callback_data=f"job:{job.id}:repost",
                )
            ]]),
        )
    await callback.answer()


# ── Deactivate job ────────────────────────────────────────────────────────────

async def deactivate_job(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
    job_id_str = callback.data.split(":")[1]
    job = await session.get(Job, _parse_uuid(job_id_str))
    if not job or job.status != JobStatus.OPEN:
        await callback.answer("Вакансія вже неактивна.", show_alert=True)
        return
    job.status = JobStatus.CANCELLED
    await session.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "📵 Вакансію деактивовано та переміщено в архів.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📋 Активні вакансії", callback_data="employer:my_jobs")],
            [types.InlineKeyboardButton(text="◀️ Меню",             callback_data="employer:panel")],
        ]),
    )
    await callback.answer()


# ── Repost from archive ───────────────────────────────────────────────────────

async def repost_job(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    job_id_str = callback.data.split(":")[1]
    job = await session.get(Job, _parse_uuid(job_id_str))
    if not job:
        await callback.answer("Вакансію не знайдено.", show_alert=True)
        return
    is_permanent = job.job_type == JobType.PERMANENT
    await state.update_data(
        city=job.city,
        job_type=job.job_type.value,
        description=job.description,
        pay_description=job.pay_description,
        workers_needed=job.workers_needed,
        location=job.location,
    )
    if is_permanent:
        await callback.message.answer("📅 Коли можна приступити? (ДД.ММ.РРРР):")
    else:
        await callback.message.answer(
            "⏰ Дата та час нової роботи (ДД.ММ.РРРР ГГ:ХХ):\n\n"
            "<i>Решта даних з попереднього оголошення скопійовано.</i>"
        )
    await state.set_state(CreateJobFSM.scheduled_time)
    await callback.answer()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_user_name(session: AsyncSession, telegram_id: int) -> str:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        return f"#{telegram_id}"
    name = user.first_name or ""
    if user.last_name:
        name += f" {user.last_name}"
    if user.username:
        name += f" (@{user.username})"
    return name.strip() or f"#{telegram_id}"


def _worker_buttons(telegram_id: int, app_id: int | None = None, pending: bool = False) -> list:
    url = f"https://t.me/{telegram_id}" if False else f"tg://user?id={telegram_id}"
    row1 = [
        types.InlineKeyboardButton(text="✉️ Написати",    url=url),
        types.InlineKeyboardButton(text="🚫 Заблокувати", callback_data=f"block:{telegram_id}"),
    ]
    if pending and app_id:
        return [
            [
                types.InlineKeyboardButton(text="✅ Прийняти",  callback_data=f"app:{app_id}:accept"),
                types.InlineKeyboardButton(text="❌ Відхилити", callback_data=f"app:{app_id}:reject"),
            ],
            row1,
        ]
    return [row1]


# ── Applicants list (3 groups) ────────────────────────────────────────────────

async def job_applicants(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
    job_id_str = callback.data.split(":")[1]
    job = await session.get(Job, _parse_uuid(job_id_str))
    if not job:
        await callback.answer("Вакансію не знайдено.", show_alert=True)
        return

    result = await session.execute(
        select(Application)
        .where(Application.job_id == job.id)
        .order_by(Application.applied_at)
    )
    apps = list(result.scalars().all())

    if not apps:
        await _safe_edit(
            callback.message,
            "👥 Заявок на цю вакансію ще немає.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")
            ]]),
        )
        await callback.answer()
        return

    pending  = [a for a in apps if a.status == ApplicationStatus.PENDING]
    accepted = [a for a in apps if a.status == ApplicationStatus.ACCEPTED]
    rejected = [a for a in apps if a.status == ApplicationStatus.REJECTED]

    await callback.message.answer(
        f"👥 <b>Заявки: {job.city} | {job.pay_description}</b>\n"
        f"🟡 Нових: {len(pending)} | 🟢 Прийнято: {len(accepted)}/{job.workers_needed} | 🔴 Відхилено: {len(rejected)}"
    )

    if pending:
        await callback.message.answer("🟡 <b>Нові заявки:</b>")
        for app in pending:
            name = await _get_user_name(session, app.worker_telegram_id)
            await callback.message.answer(
                f"👤 <b>{name}</b>\nПодано: {app.applied_at.strftime('%d.%m %H:%M')}",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=_worker_buttons(app.worker_telegram_id, app.id, pending=True)
                ),
            )

    if accepted:
        await callback.message.answer("🟢 <b>Прийняті:</b>")
        for app in accepted:
            name = await _get_user_name(session, app.worker_telegram_id)
            confirmed = app.confirmed_at.strftime('%d.%m %H:%M') if app.confirmed_at else "—"
            await callback.message.answer(
                f"👤 <b>{name}</b>\nПрийнято: {confirmed}",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=_worker_buttons(app.worker_telegram_id)
                ),
            )

    if rejected:
        await callback.message.answer("🔴 <b>Відхилені:</b>")
        for app in rejected:
            name = await _get_user_name(session, app.worker_telegram_id)
            await callback.message.answer(
                f"👤 <b>{name}</b>",
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=_worker_buttons(app.worker_telegram_id)
                ),
            )

    await callback.answer()


# ── Active workers across all jobs ────────────────────────────────────────────

async def active_workers(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    result = await session.execute(
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .where(
            Job.bot_id == registered_bot_id,
            Application.status == ApplicationStatus.ACCEPTED,
            Job.status.in_([JobStatus.OPEN, JobStatus.ASSIGNED]),
        )
        .order_by(Application.confirmed_at.desc())
    )
    rows = result.all()

    if not rows:
        await _safe_edit(
            callback.message,
            "👷 Наразі немає активних працівників.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")
            ]]),
        )
        await callback.answer()
        return

    await callback.message.answer(f"👷 <b>Активні працівники ({len(rows)}):</b>")
    for app, job in rows:
        name = await _get_user_name(session, app.worker_telegram_id)
        time_str = job.scheduled_time.strftime("%d.%m %H:%M")
        confirmed = app.confirmed_at.strftime("%d.%m %H:%M") if app.confirmed_at else "—"
        await callback.message.answer(
            f"👤 <b>{name}</b>\n"
            f"📍 {job.city} | ⏰ {time_str}\n"
            f"📌 {job.location}\n"
            f"✅ Прийнято: {confirmed}",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=_worker_buttons(app.worker_telegram_id)
            ),
        )
    await callback.answer()


# ── Edit job ──────────────────────────────────────────────────────────────────

async def edit_job_start(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    job_id_str = callback.data.split(":")[1]
    job = await session.get(Job, _parse_uuid(job_id_str))
    if not job or job.status != JobStatus.OPEN:
        await callback.answer("Вакансія недоступна для редагування.", show_alert=True)
        return

    await state.update_data(editing_job_id=str(job.id))
    await _safe_edit(
        callback.message,
        "✏️ <b>Що редагуємо?</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📝 Опис завдання",   callback_data="jedit:description")],
            [types.InlineKeyboardButton(text="💰 Умови оплати",    callback_data="jedit:pay_description")],
            [types.InlineKeyboardButton(text="👥 Кількість місць", callback_data="jedit:workers_needed")],
            [types.InlineKeyboardButton(text="📌 Адреса",          callback_data="jedit:location")],
            [types.InlineKeyboardButton(text="◀️ Меню",            callback_data="employer:panel")],
        ]),
    )
    await state.set_state(EditJobFSM.select_field)
    await callback.answer()


EDIT_PROMPTS = {
    "description":     "📝 Введіть новий опис завдання:",
    "pay_description": f"{PAY_HINT}",
    "workers_needed":  "👥 Введіть нову кількість місць (1–50):",
    "location":        "📌 Введіть нову адресу:",
}


async def edit_field_selected(callback: types.CallbackQuery, state: FSMContext) -> None:
    field = callback.data.split(":")[1]
    await state.update_data(editing_field=field)
    await callback.message.answer(EDIT_PROMPTS[field])
    await state.set_state(EditJobFSM.waiting_value)
    await callback.answer()


async def edit_value_received(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    field = data["editing_field"]
    value = message.text.strip()

    if field == "workers_needed":
        try:
            value = int(value)
            if value < 1 or value > 50:
                raise ValueError
        except ValueError:
            await message.answer("❌ Введіть число від 1 до 50")
            return

    import uuid
    job = await session.get(Job, uuid.UUID(data["editing_job_id"]))
    if not job:
        await message.answer("❌ Вакансію не знайдено.")
        await state.clear()
        return

    setattr(job, field, value)
    await session.commit()

    field_labels = {
        "description":     "Опис",
        "pay_description": "Умови оплати",
        "workers_needed":  "Кількість місць",
        "location":        "Адреса",
    }
    await message.answer(
        f"✅ <b>{field_labels[field]}</b> оновлено.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")
        ]]),
    )
    await state.clear()


# ── Block / unblock ───────────────────────────────────────────────────────────

async def block_user(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    user_id = int(callback.data.split(":")[1])
    existing = await session.execute(
        select(BotBlockedUser).where(
            BotBlockedUser.bot_id == registered_bot_id,
            BotBlockedUser.telegram_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        await callback.answer("Користувач вже заблокований.", show_alert=True)
        return
    session.add(BotBlockedUser(bot_id=registered_bot_id, telegram_id=user_id))
    await session.commit()
    await callback.answer("🚫 Користувача заблоковано.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


async def blocked_list(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    result = await session.execute(
        select(BotBlockedUser)
        .where(BotBlockedUser.bot_id == registered_bot_id)
        .order_by(BotBlockedUser.blocked_at.desc())
    )
    blocked = list(result.scalars().all())

    if not blocked:
        await _safe_edit(
            callback.message,
            "🚫 Заблокованих користувачів немає.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")
            ]]),
        )
        await callback.answer()
        return

    await callback.message.answer(f"🚫 <b>Заблоковані ({len(blocked)}):</b>")
    for entry in blocked:
        await callback.message.answer(
            f"👤 ID: <code>{entry.telegram_id}</code>\n"
            f"Дата: {entry.blocked_at.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(
                    text="🔓 Розблокувати",
                    callback_data=f"unblock:{entry.telegram_id}",
                )
            ]]),
        )
    await callback.answer()


async def unblock_user(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    user_id = int(callback.data.split(":")[1])
    result = await session.execute(
        select(BotBlockedUser).where(
            BotBlockedUser.bot_id == registered_bot_id,
            BotBlockedUser.telegram_id == user_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        await callback.answer("Користувача не знайдено.", show_alert=True)
        return
    await session.delete(entry)
    await session.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🔓 Користувача розблоковано.", show_alert=True)


# ── Application accept / reject ───────────────────────────────────────────────

async def _get_pending_app(
    callback: types.CallbackQuery, session: AsyncSession, app_id: int
) -> Application | None:
    result = await session.execute(select(Application).where(Application.id == app_id))
    app = result.scalar_one_or_none()
    if not app:
        await callback.answer("❌ Заявку не знайдено", show_alert=True)
        return None
    if app.status != ApplicationStatus.PENDING:
        await callback.answer("Ця заявка вже оброблена", show_alert=True)
        return None
    return app


async def accept_application(
    callback: types.CallbackQuery, session: AsyncSession, state: FSMContext,
) -> None:
    app_id = int(callback.data.split(":")[1])
    application = await _get_pending_app(callback, session, app_id)
    if not application:
        return

    job = await session.get(Job, application.job_id)
    accepted_count = await session.scalar(
        select(func.count()).where(
            Application.job_id == application.job_id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    ) or 0
    if accepted_count >= job.workers_needed:
        await callback.answer(
            f"Місця вже заповнені ({accepted_count}/{job.workers_needed}).",
            show_alert=True,
        )
        return

    await state.update_data(accept_app_id=app_id)
    worker_name = await _get_user_name(session, application.worker_telegram_id)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Приймаєте <b>{worker_name}</b>.\n\n"
        "📋 Введіть деталі для працівника:\n"
        "<i>Адреса збору, точний час, що взяти з собою, "
        "контактний номер — все що йому треба знати.</i>\n\n"
        "<b>Приклад:</b>\n"
        "вул. Соборна 12, біля входу о 8:00\n"
        "Мати з собою паспорт та робочий одяг\n"
        "Питання: +380XXXXXXXXX",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="❌ Скасувати", callback_data="accept:cancel"),
        ]]),
    )
    await state.set_state(AcceptDetailsFSM.waiting_details)
    await callback.answer()


async def accept_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("↩️ Скасовано. Заявка залишається в очікуванні.")
    await callback.answer()


async def got_acceptance_details(
    message: types.Message, state: FSMContext,
    session: AsyncSession, bot: Bot,
) -> None:
    data = await state.get_data()
    app_id = data["accept_app_id"]
    await state.clear()

    result = await session.execute(select(Application).where(Application.id == app_id))
    application = result.scalar_one_or_none()
    if not application or application.status != ApplicationStatus.PENDING:
        await message.answer("❌ Заявка вже оброблена.")
        return

    job = await session.get(Job, application.job_id)
    accepted_count = await session.scalar(
        select(func.count()).where(
            Application.job_id == application.job_id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    ) or 0

    application.status = ApplicationStatus.ACCEPTED
    application.confirmed_at = datetime.now(timezone.utc)

    if accepted_count + 1 >= job.workers_needed:
        job.status = JobStatus.ASSIGNED
        await message.answer(
            f"🔒 <b>Всі {job.workers_needed} місць заповнено!</b> Вакансію закрито автоматично."
        )

    await session.commit()

    employer_url = f"tg://user?id={job.employer_telegram_id}"
    try:
        await bot.send_message(
            chat_id=application.worker_telegram_id,
            text=(
                f"🎉 <b>Вашу заявку прийнято!</b>\n\n"
                f"📍 {job.city} — {job.pay_description}\n\n"
                f"📋 <b>Деталі від роботодавця:</b>\n{message.text}\n\n"
                "Якщо є питання — напишіть роботодавцю напряму:"
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="✉️ Написати роботодавцю", url=employer_url),
            ]]),
        )
    except Exception:
        logger.warning("Could not notify worker %s", application.worker_telegram_id)

    worker_url = f"tg://user?id={application.worker_telegram_id}"
    await message.answer(
        "✅ <b>Прийнято! Деталі надіслано працівнику.</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✉️ Написати працівнику", url=worker_url)],
            [types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel")],
        ]),
    )


async def reject_application(
    callback: types.CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    app_id = int(callback.data.split(":")[1])
    application = await _get_pending_app(callback, session, app_id)
    if not application:
        return

    application.status = ApplicationStatus.REJECTED
    await session.commit()

    try:
        await bot.send_message(
            chat_id=application.worker_telegram_id,
            text="😔 На жаль, роботодавець відхилив вашу заявку. Спробуйте інші вакансії.",
        )
    except Exception:
        logger.warning("Could not notify worker %s", application.worker_telegram_id)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "❌ Заявку відхилено.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel"),
        ]]),
    )
    await callback.answer()


# ── Broadcast to accepted workers ────────────────────────────────────────────

async def broadcast_start(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    job_id_str = callback.data.split(":")[1]
    job = await session.get(Job, _parse_uuid(job_id_str))
    if not job:
        await callback.answer("Вакансію не знайдено.", show_alert=True)
        return

    accepted_count = await session.scalar(
        select(func.count()).where(
            Application.job_id == job.id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    ) or 0

    if accepted_count == 0:
        await callback.answer("Немає прийнятих працівників для розсилки.", show_alert=True)
        return

    await state.update_data(broadcast_job_id=job_id_str)
    await _safe_edit(
        callback.message,
        f"📢 <b>Розсилка по вакансії</b>\n\n"
        f"📍 {job.city} — {job.pay_description}\n"
        f"👥 Отримають повідомлення: <b>{accepted_count} працівників</b>\n\n"
        "Введіть текст повідомлення:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="❌ Скасувати", callback_data="employer:my_jobs"),
        ]]),
    )
    await state.set_state(BroadcastFSM.waiting_message)
    await callback.answer()


async def broadcast_send(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
) -> None:
    data = await state.get_data()
    job_id = _parse_uuid(data["broadcast_job_id"])
    await state.clear()

    result = await session.execute(
        select(Application.worker_telegram_id).where(
            Application.job_id == job_id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    )
    worker_ids = [row[0] for row in result.all()]

    job = await session.get(Job, job_id)
    header = f"📢 <b>Повідомлення від роботодавця</b>\n📍 {job.city} — {job.pay_description}\n\n"

    sent = 0
    failed = 0
    for worker_id in worker_ids:
        try:
            await bot.send_message(chat_id=worker_id, text=header + message.text)
            sent += 1
        except Exception:
            failed += 1

    summary = f"✅ Розсилку надіслано!\n\n📨 Доставлено: {sent}"
    if failed:
        summary += f"\n⚠️ Не доставлено: {failed} (заблокували бота)"

    await message.answer(
        summary,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Меню", callback_data="employer:panel"),
        ]]),
    )


# ── Labor settings ────────────────────────────────────────────────────────────

LABOR_WELCOME = "labor_welcome"
LABOR_CONTACT = "labor_contact"


class LaborSettingsFSM(StatesGroup):
    welcome_text = State()
    contact_text = State()


async def show_settings(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    welcome = await get_cfg(session, registered_bot_id, LABOR_WELCOME, "")
    contact = await get_cfg(session, registered_bot_id, LABOR_CONTACT, "")
    welcome_preview = (welcome[:60] + "…") if len(welcome) > 60 else (welcome or "<i>не вказано</i>")
    contact_preview = (contact[:60] + "…") if len(contact) > 60 else (contact or "<i>не вказано</i>")
    await _safe_edit(
        callback.message,
        f"⚙️ <b>Налаштування бота</b>\n\n"
        f"👋 <b>Вітальний текст:</b>\n{welcome_preview}\n\n"
        f"📞 <b>Контакти:</b>\n{contact_preview}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✏️ Змінити вітальний текст", callback_data="settings:welcome")],
            [types.InlineKeyboardButton(text="📞 Змінити контакти",        callback_data="settings:contact")],
            [types.InlineKeyboardButton(text="◀️ Меню",                    callback_data="employer:panel")],
        ]),
    )
    await callback.answer()


async def settings_set_welcome(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _safe_edit(
        callback.message,
        "✏️ Введіть новий вітальний текст для працівників.\n\n"
        "<i>Підтримується HTML: &lt;b&gt;жирний&lt;/b&gt;, &lt;i&gt;курсив&lt;/i&gt;</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="❌ Скасувати", callback_data="employer:settings")
        ]]),
    )
    await state.set_state(LaborSettingsFSM.welcome_text)
    await callback.answer()


async def settings_got_welcome(
    message: types.Message, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, LABOR_WELCOME, message.text.strip())
    await state.clear()
    await message.answer(
        "✅ Вітальний текст оновлено!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Налаштування", callback_data="employer:settings")
        ]]),
    )


async def settings_set_contact(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _safe_edit(
        callback.message,
        "📞 Введіть контактну інформацію для працівників.\n\n"
        "<i>Наприклад: номер телефону, посилання на Telegram, або будь-який текст.</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="❌ Скасувати", callback_data="employer:settings")
        ]]),
    )
    await state.set_state(LaborSettingsFSM.contact_text)
    await callback.answer()


async def settings_got_contact(
    message: types.Message, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    await set_cfg(session, registered_bot_id, LABOR_CONTACT, message.text.strip())
    await state.clear()
    await message.answer(
        "✅ Контакти оновлено!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Налаштування", callback_data="employer:settings")
        ]]),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_uuid(s: str):
    import uuid
    try:
        return uuid.UUID(s)
    except ValueError:
        return None


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_menu_employer, Command("menu"))
    dp.message.register(cmd_menu_employer, Command("back"))

    # Panel
    dp.callback_query.register(employer_panel,    F.data == "employer:panel")
    dp.callback_query.register(my_jobs,           F.data == "employer:my_jobs")
    dp.callback_query.register(archive,           F.data == "employer:archive")
    dp.callback_query.register(active_workers,    F.data == "employer:active_workers")
    dp.callback_query.register(blocked_list,      F.data == "employer:blocked")

    # Broadcast
    dp.callback_query.register(broadcast_start, F.data.regexp(r"^job:[^:]+:broadcast$"))
    dp.message.register(broadcast_send,         BroadcastFSM.waiting_message)

    # Labor settings
    dp.callback_query.register(show_settings,       F.data == "employer:settings")
    dp.callback_query.register(settings_set_welcome, F.data == "settings:welcome")
    dp.callback_query.register(settings_set_contact, F.data == "settings:contact")
    dp.message.register(settings_got_welcome,       LaborSettingsFSM.welcome_text)
    dp.message.register(settings_got_contact,       LaborSettingsFSM.contact_text)

    # Create job FSM
    dp.callback_query.register(start_create_job,  F.data == "role:employer")
    dp.callback_query.register(got_job_type,      F.data.startswith("jtype:"),    CreateJobFSM.job_type)
    dp.message.register(got_city,                 CreateJobFSM.city)
    dp.message.register(got_description,          CreateJobFSM.description)
    dp.message.register(got_pay_description,      CreateJobFSM.pay_description)
    dp.message.register(got_workers_needed,       CreateJobFSM.workers_needed)
    dp.message.register(got_location,             CreateJobFSM.location)
    dp.message.register(got_time,                 CreateJobFSM.scheduled_time)
    dp.callback_query.register(confirm_job,       F.data == "job:confirm",        CreateJobFSM.confirm)
    dp.callback_query.register(cancel_job,        F.data == "job:cancel",         CreateJobFSM.confirm)

    # Job actions
    dp.callback_query.register(job_applicants,    F.data.regexp(r"^job:[^:]+:applicants$"))
    dp.callback_query.register(edit_job_start,    F.data.regexp(r"^job:[^:]+:edit$"))
    dp.callback_query.register(deactivate_job,    F.data.regexp(r"^job:[^:]+:deactivate$"))
    dp.callback_query.register(repost_job,        F.data.regexp(r"^job:[^:]+:repost$"))

    # Edit FSM
    dp.callback_query.register(edit_field_selected, F.data.startswith("jedit:"), EditJobFSM.select_field)
    dp.message.register(edit_value_received,       EditJobFSM.waiting_value)

    # Applications
    dp.callback_query.register(accept_application, F.data.regexp(r"^app:\d+:accept$"))
    dp.callback_query.register(accept_cancel,      F.data == "accept:cancel",          AcceptDetailsFSM.waiting_details)
    dp.message.register(got_acceptance_details,    AcceptDetailsFSM.waiting_details)
    dp.callback_query.register(reject_application, F.data.regexp(r"^app:\d+:reject$"))

    # Block
    dp.callback_query.register(block_user,         F.data.regexp(r"^block:\d+$"))
    dp.callback_query.register(unblock_user,       F.data.regexp(r"^unblock:\d+$"))
