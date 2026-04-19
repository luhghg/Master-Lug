"""
Запусти цей скрипт один раз щоб створити всі таблиці в БД.
Використовуй замість `alembic upgrade head` якщо є проблеми на Windows.
"""
import asyncio
import sys

# КРИТИЧНО для Windows: asyncpg не працює з ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy.ext.asyncio import create_async_engine

from app.models.base import Base
import app.models  # noqa — реєструє всі моделі в Base.metadata
from app.core.config import settings


async def init_db() -> None:
    # Прибираємо ?ssl=disable з URL — передаємо ssl=False напряму
    url = settings.DATABASE_URL.replace("?ssl=disable", "").replace("&ssl=disable", "")

    engine = create_async_engine(
        url,
        connect_args={"ssl": False},  # явно вимикаємо SSL для Docker
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("✅ Всі таблиці створено успішно!")
    print("   - users")
    print("   - registered_bots")
    print("   - jobs")
    print("   - applications")


if __name__ == "__main__":
    asyncio.run(init_db())
