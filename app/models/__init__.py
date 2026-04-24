# Import all models here so Alembic autogenerate picks them up via Base.metadata
from app.models.application import Application
from app.models.blocked_user import BotBlockedUser
from app.models.bot import RegisteredBot
from app.models.bot_config import BotConfig
from app.models.job import Job
from app.models.tattoo import BotSubscription, TattooBooking, TattooPortfolio, TattooReview
from app.models.user import User
from app.models.whitelist import PlatformWhitelist

__all__ = [
    "User", "RegisteredBot", "Job", "Application", "BotBlockedUser",
    "BotConfig", "PlatformWhitelist",
    "TattooPortfolio", "TattooReview", "TattooBooking", "BotSubscription",
]
