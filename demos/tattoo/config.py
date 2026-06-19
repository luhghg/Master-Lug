import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN is not set.\n"
        "Скопіюй .env.example → .env і встав токен від @BotFather."
    )
