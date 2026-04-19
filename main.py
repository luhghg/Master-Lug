"""
Arete Dispatcher — FastAPI entry point.
"""

import logging
import sys
from contextlib import asynccontextmanager

# asyncpg не сумісний з ProactorEventLoop (дефолт на Windows)
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.master_webhook import router as master_webhook_router
from app.api.webhook import router as webhook_router
from app.bot.master.dispatcher import get_master_bot
from app.core import app_state
from app.core.config import settings
from app.core.redis_client import close_redis, get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("Starting Arete Dispatcher...")
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
    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    await close_redis()
    logger.info("Arete Dispatcher stopped.")


app = FastAPI(
    title="Arete Dispatcher",
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
async def health_check() -> dict:
    return {"status": "healthy", "service": "Arete Dispatcher"}
