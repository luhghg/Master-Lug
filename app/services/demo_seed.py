"""Auto-seed demo bots with sample data on first run."""
import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus, JobType
from app.models.bot import RegisteredBot
from app.models.tattoo import BotSubscription
from app.services.config_service import (
    CATEGORIES, SOCIAL_TEXT, TIME_SLOTS, WELCOME_TEXT,
    get_json, set_cfg, set_json,
)

# Stable placeholder images for demo portfolio (picsum seed = consistent image)
_DEMO_PORTFOLIO = [
    dict(
        style="realism",
        photo_url="https://picsum.photos/seed/masterlug_p1/800/600",
        description="Реалістичний портрет — детальна передача тіней, світла та текстури шкіри. Виконано в 2 сеанси, загальний час 6 годин.",
        work_time="6 годин (2 сеанси)",
        price="від 4 000 грн",
    ),
    dict(
        style="geometric",
        photo_url="https://picsum.photos/seed/masterlug_p2/800/600",
        description="Геометричний орнамент на передпліччі. Чіткі лінії, симетрія, абсолютний blackwork без напівтонів.",
        work_time="3 години",
        price="від 2 000 грн",
    ),
]

logger = logging.getLogger(__name__)

_LABOR_JOBS = [
    dict(
        job_type=JobType.ONETIME,
        city="Київ",
        description=(
            "Потрібні вантажники для розвантаження меблів та техніки при переїзді офісу. "
            "Робота фізична, але без спеціальних навичок. Обід за рахунок роботодавця. "
            "Оплата готівкою в кінці дня."
        ),
        pay_description="1 200 грн/день + обід",
        workers_needed=4,
        location="вул. Хрещатик 22, офіс 3 поверх — зібратись о 8:45 біля входу",
        scheduled_time=datetime(2026, 8, 10, 9, 0),
    ),
    dict(
        job_type=JobType.PERMANENT,
        city="Київ",
        description=(
            "Шукаємо комірника на склад інтернет-магазину. "
            "Обов'язки: приймання товару, сортування, видача кур'єрам. "
            "Графік Пн–Пт 09:00–18:00, є обідня перерва. "
            "Офіційне оформлення, своєчасна виплата зарплати."
        ),
        pay_description="18 000 грн/міс + премії",
        workers_needed=1,
        location="вул. Бориспільська 9, склад LogiHub (є маршрутка від ст. м. Харківська)",
        scheduled_time=datetime(2026, 8, 1, 0, 0),
    ),
]

_BEAUTY_CATEGORIES = [
    {"key": "realism",    "name": "🖤 Реалізм"},
    {"key": "geometric",  "name": "📐 Геометрія"},
    {"key": "watercolor", "name": "🎨 Акварель"},
    {"key": "blackwork",  "name": "⬛ Blackwork"},
    {"key": "japanese",   "name": "🐉 Японський стиль"},
]


_LABOR_SEED_COUNT = len(_LABOR_JOBS)


async def seed_labor_demo(session: AsyncSession, bot_id: int) -> None:
    logger.info("seed_labor_demo called for bot_id=%d", bot_id)
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        logger.warning("seed_labor_demo: bot_id=%d not found in DB", bot_id)
        return

    if not bot.is_active:
        bot.is_active = True
        await session.commit()

    # Clear all subscriptions so nobody gets spam notifications from demo
    await session.execute(delete(BotSubscription).where(BotSubscription.bot_id == bot_id))
    await session.commit()

    seeded = await session.scalar(
        select(func.count(Job.id)).where(Job.bot_id == bot_id, Job.employer_telegram_id == 0)
    )

    if seeded == _LABOR_SEED_COUNT:
        return  # already seeded correctly

    # Delete all jobs for this bot and re-seed
    await session.execute(delete(Job).where(Job.bot_id == bot_id))
    await session.commit()
    logger.info("Cleared stale demo jobs for bot_id=%d, re-seeding", bot_id)

    for data in _LABOR_JOBS:
        session.add(Job(
            bot_id=bot_id,
            employer_telegram_id=0,  # 0 = demo seed marker, visible to all
            status=JobStatus.OPEN,
            **data,
        ))
    await session.commit()
    logger.info("Seeded %d demo jobs for bot_id=%d", _LABOR_SEED_COUNT, bot_id)


async def seed_beauty_demo(
    session: AsyncSession,
    bot_id: int,
    bot=None,
    owner_id: int = 0,
) -> None:
    record = await session.get(RegisteredBot, bot_id)
    if not record:
        return

    if not record.is_active:
        record.is_active = True
        await session.commit()

    # Clear all subscriptions so nobody gets spam notifications from demo
    await session.execute(delete(BotSubscription).where(BotSubscription.bot_id == bot_id))
    await session.commit()

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

    # Seed demo portfolio photos — requires live bot + owner chat
    if bot and owner_id:
        from app.models.tattoo import TattooPortfolio
        count = await session.scalar(
            select(func.count(TattooPortfolio.id)).where(TattooPortfolio.bot_id == bot_id)
        )
        if count:
            return  # already seeded

        seeded = 0
        for item in _DEMO_PORTFOLIO:
            try:
                # Send silently to owner to obtain a permanent Telegram file_id
                msg = await bot.send_photo(
                    chat_id=owner_id,
                    photo=item["photo_url"],
                    caption="📌 Автосід демо-портфоліо (технічне повідомлення)",
                    disable_notification=True,
                )
                file_id = msg.photo[-1].file_id
                try:
                    await bot.delete_message(owner_id, msg.message_id)
                except Exception:
                    pass
                session.add(TattooPortfolio(
                    bot_id=bot_id,
                    style=item["style"],
                    photo_id=file_id,
                    description=item["description"],
                    work_time=item["work_time"],
                    price=item["price"],
                    view_count=0,
                ))
                seeded += 1
            except Exception as e:
                logger.warning("Could not seed portfolio item '%s': %s", item["style"], e)

        if seeded:
            await session.commit()
            logger.info("Seeded %d demo portfolio items for bot_id=%d", seeded, bot_id)
