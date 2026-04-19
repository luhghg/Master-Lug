import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.bot.master.dispatcher import process_master_update
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/master-webhook")
async def handle_master_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if x_telegram_bot_api_secret_token != settings.SECRET_WEBHOOK_TOKEN:
        logger.warning("Rejected master webhook: invalid secret token")
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        update_data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    await process_master_update(update_data)
    return {"status": "ok"}
