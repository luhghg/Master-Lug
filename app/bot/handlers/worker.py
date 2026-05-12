import logging
import uuid

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application, ApplicationStatus
from app.models.job import Job, JobStatus
from app.services.config_service import is_demo_bot
from app.services.job_service import list_bot_jobs
from app.services.rating import get_cached_rating, is_user_eligible

logger = logging.getLogger(__name__)

LABOR_WELCOME = "labor_welcome"
LABOR_CONTACT = "labor_contact"

DEFAULT_WELCOME = "👷 <b>Ласкаво просимо!</b>\n\nОберіть що вас цікавить:"


# ── UI helpers ────────────────────────────────────────────────────────────────

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


async def _worker_home_kb(session: AsyncSession, bot_id: int) -> types.InlineKeyboardMarkup:
    from app.services.config_service import get_cfg
    contact = await get_cfg(session, bot_id, LABOR_CONTACT, "")
    rows = [[types.InlineKeyboardButton(text="📋 Актуальні вакансії", callback_data="worker:jobs")]]
    if contact:
        rows.append([types.InlineKeyboardButton(text="📞 Контакти", callback_data="worker:contacts")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _back_home_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="◀️ Меню", callback_data="worker:home")
    ]])


# ── Worker home (menu) ────────────────────────────────────────────────────────

async def show_worker_home(message: types.Message, session: AsyncSession, bot_id: int) -> None:
    """Called from /start and /menu — always sends new message."""
    from app.services.config_service import get_cfg
    text = await get_cfg(session, bot_id, LABOR_WELCOME, DEFAULT_WELCOME)
    kb = await _worker_home_kb(session, bot_id)
    await message.answer(text, reply_markup=kb)


async def worker_home_callback(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    from app.services.config_service import get_cfg
    text = await get_cfg(session, registered_bot_id, LABOR_WELCOME, DEFAULT_WELCOME)
    kb = await _worker_home_kb(session, registered_bot_id)
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def cmd_menu_worker(
    message: types.Message, state: FSMContext,
    session: AsyncSession, registered_bot_id: int,
) -> None:
    await state.clear()
    await show_worker_home(message, session, registered_bot_id)


# ── Jobs list ─────────────────────────────────────────────────────────────────

async def show_all_jobs(
    callback: types.CallbackQuery,
    session: AsyncSession,
    registered_bot_id: int,
) -> None:
    if not await is_user_eligible(callback.from_user.id, session):
        rating = await get_cached_rating(callback.from_user.id, session)
        await callback.answer(
            f"⛔ Ваш рейтинг {rating:.1f}/5.0 нижче мінімального (2.0). Тимчасово заблоковано.",
            show_alert=True,
        )
        return

    jobs = await list_bot_jobs(session, registered_bot_id)

    if not jobs:
        await _safe_edit(
            callback.message,
            "😔 <b>Наразі вакансій немає.</b>\n\nПоверніться пізніше — роботодавець додасть нові!",
            reply_markup=_back_home_kb(),
        )
        await callback.answer()
        return

    type_labels = {"ONETIME": "💵 Разова", "PERMANENT": "📅 Постійна"}

    lines = []
    for i, job in enumerate(jobs, 1):
        t = type_labels.get(job.job_type.value, "💼")
        is_perm = job.job_type.value == "PERMANENT"
        time_str = job.scheduled_time.strftime("%d.%m.%Y") if is_perm else job.scheduled_time.strftime("%d.%m.%Y %H:%M")
        lines.append(f"{i}. {t}  {job.city}\n    💰 {job.pay_description}\n    ⏰ {time_str}")

    jobs_block = "\n\n".join(lines)

    await _safe_edit(
        callback.message,
        f"📋 <b>Актуальні вакансії ({len(jobs)})</b>\n\n"
        f"<blockquote>{jobs_block}</blockquote>\n\n"
        f"Натисніть щоб відкрити деталі 👇",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{i}. {type_labels.get(job.job_type.value, '💼')} {job.city}",
                callback_data=f"worker:job:{job.id}",
            )]
            for i, job in enumerate(jobs, 1)
        ] + [[types.InlineKeyboardButton(text="◀️ Меню", callback_data="worker:home")]]),
    )
    await callback.answer()


async def show_job_detail(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
    job_id_str = callback.data.split(":", 2)[2]
    try:
        job_id = uuid.UUID(job_id_str)
    except ValueError:
        await callback.answer("❌ Невірний ID", show_alert=True)
        return

    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job or job.status != JobStatus.OPEN:
        await callback.answer("❌ Вакансія вже недоступна", show_alert=True)
        return

    is_permanent = job.job_type.value == "PERMANENT"
    time_label = "Початок" if is_permanent else "Дата та час"
    time_str = job.scheduled_time.strftime("%d.%m.%Y") if is_permanent else job.scheduled_time.strftime("%d.%m.%Y %H:%M")

    accepted = await session.scalar(
        select(func.count()).where(
            Application.job_id == job_id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    ) or 0
    spots_left = job.workers_needed - accepted

    text = (
        f"💼 <b>Вакансія</b>\n\n"
        f"📍 Місто: {job.city}\n"
        f"📌 Адреса: {job.location}\n"
        f"⏰ {time_label}: {time_str}\n"
        f"💰 Оплата: {job.pay_description}\n"
        f"👥 Вільних місць: {spots_left}/{job.workers_needed}\n\n"
        f"📝 {job.description}"
    )

    await _safe_edit(
        callback.message,
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Відгукнутись", callback_data=f"apply:{job.id}")],
            [types.InlineKeyboardButton(text="◀️ Назад до списку", callback_data="worker:jobs")],
        ]),
    )
    await callback.answer()


# ── Contacts ──────────────────────────────────────────────────────────────────

async def show_contacts(
    callback: types.CallbackQuery, session: AsyncSession, registered_bot_id: int,
) -> None:
    from app.services.config_service import get_cfg
    contact = await get_cfg(session, registered_bot_id, LABOR_CONTACT, "")
    if not contact:
        await callback.answer("Контакти не вказані", show_alert=True)
        return
    await _safe_edit(callback.message, contact, reply_markup=_back_home_kb())
    await callback.answer()


# ── Apply for job ─────────────────────────────────────────────────────────────

async def apply_for_job(
    callback: types.CallbackQuery, session: AsyncSession, bot: Bot, registered_bot_id: int,
) -> None:
    job_id_str = callback.data.split(":", 1)[1]
    try:
        job_id = uuid.UUID(job_id_str)
    except ValueError:
        await callback.answer("❌ Невірний ID вакансії", show_alert=True)
        return

    job_result = await session.execute(select(Job).where(Job.id == job_id))
    job = job_result.scalar_one_or_none()
    if not job or job.status != JobStatus.OPEN:
        await callback.answer("❌ Ця вакансія вже недоступна", show_alert=True)
        return

    dup = await session.execute(
        select(Application).where(
            Application.job_id == job_id,
            Application.worker_telegram_id == callback.from_user.id,
        )
    )
    if dup.scalar_one_or_none():
        await callback.answer("Ви вже подали заявку на цю вакансію!", show_alert=True)
        return

    accepted_count = await session.scalar(
        select(func.count()).where(
            Application.job_id == job_id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    ) or 0
    if accepted_count >= job.workers_needed:
        employer_url = f"tg://user?id={job.employer_telegram_id}"
        await _safe_edit(
            callback.message,
            f"😔 <b>Всі місця вже заповнені</b> ({accepted_count}/{job.workers_needed}).\n\n"
            "Але ви можете написати роботодавцю напряму — можливо він прийме ще.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✉️ Написати роботодавцю", url=employer_url)],
                [types.InlineKeyboardButton(text="◀️ Назад до списку", callback_data="worker:jobs")],
            ]),
        )
        await callback.answer()
        return

    application = Application(
        job_id=job_id,
        worker_telegram_id=callback.from_user.id,
        status=ApplicationStatus.PENDING,
    )
    session.add(application)
    await session.commit()
    await session.refresh(application)

    await _safe_edit(
        callback.message,
        "✅ <b>Заявку подано!</b>\n\nРоботодавець розгляне її та зв'яжеться з вами.\n⏰ Будьте готові вчасно!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📋 Переглянути всі вакансії", callback_data="worker:jobs")],
            [types.InlineKeyboardButton(text="◀️ Меню", callback_data="worker:home")],
        ]),
    )
    await callback.answer()

    user = callback.from_user
    name = user.full_name
    mention = f"@{user.username}" if user.username else f"#{user.id}"
    worker_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

    demo = is_demo_bot(registered_bot_id)
    notify_id = callback.from_user.id if demo else job.employer_telegram_id
    prefix = "📬 <b>Так виглядає повідомлення роботодавцю:</b>\n\n" if demo else ""
    try:
        await bot.send_message(
            chat_id=notify_id,
            text=(
                f"{prefix}"
                f"🔔 <b>Нова заявка на вашу вакансію!</b>\n\n"
                f"📍 {job.city}\n"
                f"💰 {job.pay_description}\n"
                f"📌 {job.location}\n\n"
                f"👤 Працівник: <b>{name}</b> ({mention})"
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✉️ Написати працівнику", url=worker_url)],
                [
                    types.InlineKeyboardButton(text="✅ Прийняти",  callback_data=f"app:{application.id}:accept"),
                    types.InlineKeyboardButton(text="❌ Відхилити", callback_data=f"app:{application.id}:reject"),
                ],
            ]),
        )
    except Exception:
        logger.warning("Could not notify employer %s about application %s", job.employer_telegram_id, application.id)


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_menu_worker, Command("menu"))
    dp.message.register(cmd_menu_worker, Command("back"))
    dp.callback_query.register(worker_home_callback, F.data == "worker:home")
    dp.callback_query.register(show_all_jobs,        F.data == "worker:jobs")
    dp.callback_query.register(show_job_detail,      F.data.startswith("worker:job:"))
    dp.callback_query.register(show_contacts,        F.data == "worker:contacts")
    dp.callback_query.register(apply_for_job,        F.data.startswith("apply:"))
