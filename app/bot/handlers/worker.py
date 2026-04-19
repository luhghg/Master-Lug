import logging
import uuid

from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.application import Application, ApplicationStatus
from app.models.job import Job, JobStatus
from app.services.job_service import list_open_jobs
from app.services.rating import get_cached_rating, is_user_eligible

logger = logging.getLogger(__name__)


class WorkerFSM(StatesGroup):
    select_city = State()


# ── Entry ─────────────────────────────────────────────────────────────────────

async def start_worker(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    await callback.message.answer("📍 В якому місті шукаєте роботу?")
    await state.set_state(WorkerFSM.select_city)
    await callback.answer()


# ── City → Job list ───────────────────────────────────────────────────────────

async def got_city(
    message: types.Message, state: FSMContext, session: AsyncSession
) -> None:
    city = message.text.strip()

    # Check worker eligibility based on global reputation
    if not await is_user_eligible(message.from_user.id, session):
        rating = await get_cached_rating(message.from_user.id, session)
        await message.answer(
            f"⛔ Ваш рейтинг <b>{rating:.1f} / 5.0</b> нижче мінімально допустимого (2.0).\n"
            "Ви тимчасово заблоковані від нових замовлень.",
            parse_mode="HTML",
        )
        await state.clear()
        return

    jobs = await list_open_jobs(session, city)
    if not jobs:
        await message.answer(f"😔 В місті <b>{city}</b> немає відкритих вакансій.", parse_mode="HTML")
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"📋 Знайдено <b>{len(jobs)}</b> вакансій у {city}:",
        parse_mode="HTML",
    )

    for job in jobs:
        await message.answer(
            f"<b>💼 Вакансія</b>\n"
            f"📍 {job.city} | ⏰ {job.scheduled_time.strftime('%d.%m %H:%M')}\n"
            f"💰 {job.pay_description}\n"
            f"📌 {job.location}\n\n"
            f"📝 {job.description}",
            parse_mode="HTML",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✅ Відгукнутись", callback_data=f"apply:{job.id}"
                        )
                    ]
                ]
            ),
        )


# ── Apply for job ─────────────────────────────────────────────────────────────

async def apply_for_job(
    callback: types.CallbackQuery, session: AsyncSession, bot: Bot
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

    # Check quota: count already accepted workers
    accepted_count = await session.scalar(
        select(func.count()).where(
            Application.job_id == job_id,
            Application.status == ApplicationStatus.ACCEPTED,
        )
    ) or 0
    if accepted_count >= job.workers_needed:
        employer_url = f"tg://user?id={job.employer_telegram_id}"
        await callback.message.answer(
            f"😔 <b>Нажаль, всі місця вже заповнені</b> ({accepted_count}/{job.workers_needed}).\n\n"
            "Але ви можете написати роботодавцю напряму — можливо він прийме ще.",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✉️ Написати роботодавцю",
                            url=employer_url,
                        )
                    ]
                ]
            ),
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

    await callback.message.answer(
        "✅ <b>Заявку подано!</b>\n\n"
        "Роботодавець розгляне її та зв'яжеться з вами.\n"
        "⏰ Будьте готові вчасно!",
        parse_mode="HTML",
    )
    await callback.answer()

    # Notify employer
    user = callback.from_user
    name = user.full_name
    mention = f"@{user.username}" if user.username else f"#{user.id}"
    worker_url = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"
    try:
        await bot.send_message(
            chat_id=job.employer_telegram_id,
            text=(
                f"🔔 <b>Нова заявка на вашу вакансію!</b>\n\n"
                f"📍 {job.city}\n"
                f"💰 {job.pay_description}\n"
                f"📌 {job.location}\n\n"
                f"👤 Працівник: <b>{name}</b> ({mention})\n\n"
                f"<i>Ви можете написати йому напряму, уточнити деталі, "
                f"і лише потім прийняти або відхилити.</i>"
            ),
            parse_mode="HTML",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="✉️ Написати працівнику",
                            url=worker_url,
                        ),
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="✅ Прийняти",
                            callback_data=f"app:{application.id}:accept",
                        ),
                        types.InlineKeyboardButton(
                            text="❌ Відхилити",
                            callback_data=f"app:{application.id}:reject",
                        ),
                    ],
                ]
            ),
        )
    except Exception:
        logger.warning(
            "Could not notify employer %s about application %s",
            job.employer_telegram_id,
            application.id,
        )


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.callback_query.register(start_worker, F.data == "role:worker")
    dp.message.register(got_city, WorkerFSM.select_city)
    dp.callback_query.register(apply_for_job, F.data.startswith("apply:"))
