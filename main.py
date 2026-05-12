"""
MasterLug Dispatcher — FastAPI entry point.
"""

import logging
import sys
from contextlib import asynccontextmanager

import sentry_sdk

# asyncpg не сумісний з ProactorEventLoop (дефолт на Windows)
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from aiogram import types
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.master_webhook import router as master_webhook_router
from app.api.webhook import router as webhook_router
from app.bot.master.dispatcher import get_master_bot
from app.core import app_state
from app.core.config import settings
from app.core.redis_client import close_redis, get_redis

from app.core.config import settings as _settings_early
if _settings_early.SENTRY_DSN:
    sentry_sdk.init(
        dsn=_settings_early.SENTRY_DSN,
        traces_sample_rate=0.1,
        environment="production",
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("Starting MasterLug Dispatcher...")
    await get_redis()
    logger.info("Redis connection pool ready.")
    master_bot = await get_master_bot()
    master_me = await master_bot.get_me()
    app_state.master_bot_username = master_me.username
    await master_bot.set_webhook(
        url=f"{settings.BASE_WEBHOOK_URL}/master-webhook",
        secret_token=settings.SECRET_WEBHOOK_TOKEN,
        allowed_updates=["message", "callback_query"],
    )
    logger.info("Master bot @%s webhook set.", app_state.master_bot_username)

    # Commands for all users of master bot
    await master_bot.set_my_commands([
        types.BotCommand(command="start", description="🏠 Головне меню"),
        types.BotCommand(command="menu",  description="🏠 Головне меню"),
    ])
    # /admin visible only to platform owner
    if settings.PLATFORM_OWNER_ID:
        try:
            await master_bot.set_my_commands(
                commands=[
                    types.BotCommand(command="start", description="🏠 Головне меню"),
                    types.BotCommand(command="menu",  description="🏠 Головне меню"),
                    types.BotCommand(command="admin", description="🛠 Панель адміна"),
                ],
                scope=types.BotCommandScopeChat(chat_id=settings.PLATFORM_OWNER_ID),
            )
        except Exception as e:
            logger.warning("Could not set owner commands (send /start to master bot first): %s", e)
    logger.info("Master bot commands set.")

    # Set commands for all existing active sub-bots
    from aiogram import Bot as AiogramBot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from sqlalchemy import select as sa_select
    from app.core.database import AsyncSessionLocal
    from app.core.security import decrypt_token
    from app.models.bot import RegisteredBot

    sub_commands = [
        types.BotCommand(command="start", description="🏠 Головне меню"),
        types.BotCommand(command="menu",  description="📋 Відкрити меню"),
        types.BotCommand(command="back",  description="◀️ Повернутись до меню"),
    ]
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa_select(RegisteredBot).where(RegisteredBot.is_active.is_(True))
        )
        bots = list(result.scalars().all())
    for registered in bots:
        try:
            token = decrypt_token(registered.encrypted_token)
            sub_bot = AiogramBot(
                token=token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            await sub_bot.set_my_commands(sub_commands)
            await sub_bot.session.close()
        except Exception as e:
            logger.warning("Could not set commands for @%s: %s", registered.bot_username, e)
    logger.info("Sub-bot commands updated for %d bots.", len(bots))

    # Seed demo bots with sample data (only if empty)
    from app.services.demo_seed import seed_labor_demo, seed_beauty_demo
    async with AsyncSessionLocal() as session:
        if settings.DEMO_BOT_LABOR_ID:
            await seed_labor_demo(session, settings.DEMO_BOT_LABOR_ID)
        if settings.DEMO_BOT_BEAUTY_ID:
            await seed_beauty_demo(session, settings.DEMO_BOT_BEAUTY_ID)

    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    await close_redis()
    logger.info("MasterLug Dispatcher stopped.")


app = FastAPI(
    title="MasterLug Dispatcher",
    description="Multi-tenant Bot-as-a-Service platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


# ── Global exception handler ──────────────────────────────────────────────────
# One sub-bot crashing must NEVER take down the whole server.

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error on %s %s: %s", request.method, request.url, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Routes ────────────────────────────────────────────────────────────────────

app.include_router(webhook_router)
app.include_router(master_webhook_router)


@app.get("/health", tags=["ops"])
async def health_check() -> JSONResponse:
    from app.core.database import AsyncSessionLocal
    from app.core.redis_client import get_redis
    from sqlalchemy import text

    checks: dict[str, str] = {}

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        logger.error("Health DB check failed: %s", e)
        checks["db"] = "error"

    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        logger.error("Health Redis check failed: %s", e)
        checks["redis"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
    )
