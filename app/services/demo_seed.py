"""Auto-seed demo bots with sample data on first run."""
import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus, JobType
from app.models.bot import RegisteredBot
from app.services.config_service import (
    CATEGORIES, SOCIAL_TEXT, TIME_SLOTS, WELCOME_TEXT,
    get_json, set_cfg, set_json,
)

logger = logging.getLogger(__name__)

_LABOR_JOBS = [
    dict(
        job_type=JobType.ONETIME,
        city="Вінниця",
        description="Потрібні вантажники для розвантаження фури. Робота на 1 день, фізична підготовка вітається.",
        pay_description="800 грн/день + обід",
        workers_needed=3,
        location="вул. Хмельницьке шосе 12, склад №3",
        scheduled_time=datetime(2027, 3, 15, 8, 0),
    ),
    dict(
        job_type=JobType.PERMANENT,
        city="Вінниця",
        description="Прибиральник у торговий центр. Графік 2/2, ранкова зміна 06:00–14:00. Офіційне оформлення.",
        pay_description="12 000 грн/міс",
        workers_needed=1,
        location="ТРЦ 'Проспект', вул. Соборна 12",
        scheduled_time=datetime(2027, 3, 20, 0, 0),
    ),
    dict(
        job_type=JobType.ONETIME,
        city="Вінниця",
        description="Промоутери для роздачі листівок та анкетування біля ТЦ. Досвід не потрібен, навчаємо на місці.",
        pay_description="600 грн за 6 годин",
        workers_needed=5,
        location="ТЦ 'Мегамол', центральний вхід",
        scheduled_time=datetime(2027, 4, 1, 10, 0),
    ),
    dict(
        job_type=JobType.ONETIME,
        city="Вінниця",
        description="Різноробочий на будівельний об'єкт. Подача матеріалів, прибирання території.",
        pay_description="700 грн/день",
        workers_needed=2,
        location="вул. Келецька 55, новобудова",
        scheduled_time=datetime(2027, 4, 5, 7, 30),
    ),
]

_BEAUTY_CATEGORIES = [
    {"key": "realism",    "name": "🖤 Реалізм"},
    {"key": "geometric",  "name": "📐 Геометрія"},
    {"key": "watercolor", "name": "🎨 Акварель"},
    {"key": "blackwork",  "name": "⬛ Blackwork"},
    {"key": "japanese",   "name": "🐉 Японський стиль"},
]


async def seed_labor_demo(session: AsyncSession, bot_id: int) -> None:
    existing = await session.scalar(
        select(func.count(Job.id)).where(Job.bot_id == bot_id)
    )
    if existing and existing > 0:
        return

    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        return

    for data in _LABOR_JOBS:
        session.add(Job(
            bot_id=bot_id,
            employer_telegram_id=bot.owner_telegram_id,
            status=JobStatus.OPEN,
            **data,
        ))
    await session.commit()
    logger.info("Seeded %d demo jobs for bot_id=%d", len(_LABOR_JOBS), bot_id)


async def seed_beauty_demo(session: AsyncSession, bot_id: int) -> None:
    cats = await get_json(session, bot_id, CATEGORIES, None)
    if not cats:
        await set_json(session, bot_id, CATEGORIES, _BEAUTY_CATEGORIES)

    await set_cfg(
        session, bot_id, WELCOME_TEXT,
        "👋 <b>Ласкаво просимо до тату-студії!</b>\n\n"
        "Переглядайте портфоліо, записуйтесь на сеанс або залишайте відгуки.\n\n"
        "<i>Це демо-бот платформи MasterLug — такий самий отримає ваш бізнес.</i>",
    )
    await set_cfg(
        session, bot_id, SOCIAL_TEXT,
        "📱 <b>Контакти студії</b>\n\n"
        "Instagram: @demo_tattoo_studio\n"
        "Telegram: @demo_master\n"
        "Телефон: +380 XX XXX XX XX\n\n"
        "<i>Ваші реальні контакти будуть тут.</i>",
    )

    slots = await get_json(session, bot_id, TIME_SLOTS, None)
    if not slots:
        await set_json(session, bot_id, TIME_SLOTS, ["10:00", "12:00", "14:00", "16:00", "18:00"])

    logger.info("Seeded beauty demo config for bot_id=%d", bot_id)
