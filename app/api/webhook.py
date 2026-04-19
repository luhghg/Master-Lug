import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.bot.dispatcher import process_update
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.bot_service import get_bot_by_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/{bot_token}")
async def handle_webhook(
    bot_token: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """
    Unified entry point for ALL sub-bot traffic.
    Telegram sends the secret token header so we can reject spoofed requests.
    """
    # ── 1. Validate Telegram secret header ───────────────────────────────────
    if x_telegram_bot_api_secret_token != settings.SECRET_WEBHOOK_TOKEN:
        logger.warning("Rejected webhook: invalid secret token")
        raise HTTPException(status_code=403, detail="Forbidden")

    # ── 2. Verify bot is registered in our system ─────────────────────────────
    async with AsyncSessionLocal() as session:
        registered_bot = await get_bot_by_token(session, bot_token)

    if not registered_bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    # ── 3. Parse body ─────────────────────────────────────────────────────────
    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # ── 4. Dispatch (non-blocking; errors are swallowed inside process_update) ─
    await process_update(
        plain_token=bot_token,
        registered_bot_id=registered_bot.id,
        bot_username=registered_bot.bot_username,
        owner_telegram_id=registered_bot.owner_telegram_id,
        update_data=update_data,
    )

    return {"status": "ok"}
