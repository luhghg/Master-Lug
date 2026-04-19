import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus, JobType


async def create_job(
    session: AsyncSession,
    *,
    bot_id: int,
    employer_telegram_id: int,
    job_type: JobType,
    city: str,
    description: str,
    pay_description: str,
    workers_needed: int,
    location: str,
    scheduled_time: datetime,
) -> Job:
    job = Job(
        bot_id=bot_id,
        employer_telegram_id=employer_telegram_id,
        job_type=job_type,
        city=city,
        description=description,
        pay_description=pay_description,
        workers_needed=workers_needed,
        location=location,
        scheduled_time=scheduled_time,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    result = await session.execute(select(Job).where(Job.id == job_id))
    return result.scalar_one_or_none()


async def list_open_jobs(session: AsyncSession, city: str, limit: int = 5) -> list[Job]:
    result = await session.execute(
        select(Job)
        .where(Job.city == city, Job.status == JobStatus.OPEN)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def generate_deep_link(bot_username: str, job_id: uuid.UUID) -> str:
    return f"https://t.me/{bot_username}?start=job_{job_id}"


def format_job_card(job: Job, bot_username: str) -> str:
    link = generate_deep_link(bot_username, job.id)
    is_permanent = job.job_type == JobType.PERMANENT
    type_line  = "📅 ПОСТІЙНА РОБОТА" if is_permanent else "💵 РАЗОВА РОБОТА"
    time_label = "Початок"            if is_permanent else "Дата та час"
    time_str   = job.scheduled_time.strftime("%d.%m.%Y") if is_permanent \
                 else job.scheduled_time.strftime("%d.%m.%Y %H:%M")
    return (
        f"💼 {type_line}\n\n"
        f"📍 Місто: {job.city}\n"
        f"📌 Адреса: {job.location}\n"
        f"⏰ {time_label}: {time_str}\n"
        f"💰 Оплата: {job.pay_description}\n"
        f"👥 Місць: {job.workers_needed}\n\n"
        f"📝 {job.description}\n\n"
        f"👉 Відгукнутись: {link}"
    )
