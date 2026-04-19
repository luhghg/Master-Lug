# Import all models here so Alembic autogenerate picks them up via Base.metadata
from app.models.application import Application
from app.models.blocked_user import BotBlockedUser
from app.models.bot import RegisteredBot
from app.models.job import Job
from app.models.user import User

__all__ = ["User", "RegisteredBot", "Job", "Application", "BotBlockedUser"]
