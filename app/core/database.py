from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,       # Recycles stale connections
    pool_recycle=3600,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,   # Safe for async usage
)


async def get_db() -> AsyncSession:  # noqa: RET505  (used as FastAPI dependency)
    async with AsyncSessionLocal() as session:
        yield session
