"""
Реєструє бот в БД через Docker psql (без прямого з'єднання з Python).
Обхід проблеми asyncpg + Docker Desktop на Windows.
"""
import hashlib
import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

ENCRYPTION_KEY = os.environ["ENCRYPTION_KEY"]
fernet = Fernet(ENCRYPTION_KEY.encode())

print("=== Реєстрація нового бота ===\n")
plain_token  = input("Токен бота (від BotFather): ").strip()
bot_username = input("Username бота БЕЗ @: ").strip()
owner_id     = input("Твій Telegram ID: ").strip()
print("Ніша: 1) LABOR  2) BEAUTY  3) SPORTS")
niche_choice = input("Обери номер [1]: ").strip() or "1"
niche_map = {"1": "LABOR", "2": "BEAUTY", "3": "SPORTS"}
niche = niche_map.get(niche_choice, "LABOR")

token_hash      = hashlib.sha256(plain_token.encode()).hexdigest()
encrypted_token = fernet.encrypt(plain_token.encode()).decode()

sql = (
    "INSERT INTO registered_bots "
    "(owner_telegram_id, token_hash, encrypted_token, bot_username, niche, is_active) "
    f"VALUES ({owner_id}, '{token_hash}', '{encrypted_token}', '{bot_username}', '{niche}', true) "
    "ON CONFLICT (token_hash) DO NOTHING;"
)

with open("register_bot.sql", "w", encoding="utf-8") as f:
    f.write(sql)

print("\n✅ Файл register_bot.sql створено!")
print("\nТепер виконай в терміналі:\n")
print("  Get-Content register_bot.sql | docker compose exec -T db psql -U postgres -d arete_db")
