"""Per-bot configuration stored in bot_configs table."""
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_config import BotConfig

# ── Keys ─────────────────────────────────────────────────────────────────────
SOCIAL_TEXT  = "social_text"
TIME_SLOTS   = "time_slots"    # JSON list: ["10:00", "12:00"]
CATEGORIES   = "categories"    # JSON list: [{"key": "realism", "name": "🖤 Реалізм"}, ...]
WELCOME_TEXT = "welcome_text"


async def get_cfg(session: AsyncSession, bot_id: int, key: str, default: str | None = None) -> str | None:
    result = await session.execute(
        select(BotConfig.value).where(BotConfig.bot_id == bot_id, BotConfig.key == key)
    )
    val = result.scalar_one_or_none()
    return val if val is not None else default


async def set_cfg(session: AsyncSession, bot_id: int, key: str, value: str) -> None:
    stmt = pg_insert(BotConfig).values(bot_id=bot_id, key=key, value=value)
    stmt = stmt.on_conflict_do_update(constraint="uq_bot_config", set_={"value": value})
    await session.execute(stmt)
    await session.commit()


async def get_json(session: AsyncSession, bot_id: int, key: str, default) -> list | dict:
    raw = await get_cfg(session, bot_id, key)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return default


async def set_json(session: AsyncSession, bot_id: int, key: str, value: list | dict) -> None:
    await set_cfg(session, bot_id, key, json.dumps(value, ensure_ascii=False))
