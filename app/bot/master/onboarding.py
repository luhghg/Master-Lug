import logging

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_state
from app.core.config import settings
from app.core.security import hash_token
from app.models.bot import BotNiche, RegisteredBot
from app.services.bot_service import register_bot

logger = logging.getLogger(__name__)

NICHE_LABELS: dict[BotNiche, str] = {
    BotNiche.LABOR:  "💼 Робота та підробіток",
    BotNiche.BEAUTY: "💅 Краса та тату",
    BotNiche.SPORTS: "🏋️ Спорт та фітнес",
}

# Naming convention per niche
NICHE_NAME_EXAMPLES: dict[BotNiche, str] = {
    BotNiche.LABOR:  "vinnytsia_robota_vasyl_bot",
    BotNiche.BEAUTY: "kyiv_tatu_olga_bot",
    BotNiche.SPORTS: "lviv_sport_andriy_bot",
}

NAMING_RULES = (
    "📋 <b>Правила назви бота (важливо!):</b>\n\n"
    "Формат: <code>[місто]_[ніша]_[ваше_ім'я або унікальне]_bot</code>\n\n"
    "✅ Правильно:\n"
    "• <code>vinnytsia_robota_vasyl_bot</code>\n"
    "• <code>kyiv_work_petro_bot</code>\n\n"
    "❌ Неправильно:\n"
    "• <code>robota_bot</code> — надто загальна\n"
    "• <code>vinnytsia_robota_bot</code> — хтось вже міг взяти\n"
    "• <code>mybot123</code> — нічого не говорить клієнтам\n\n"
    "<i>Унікальність гарантує Telegram — двох однакових username не буває.</i>"
)


class OnboardingFSM(StatesGroup):
    select_niche  = State()
    terms         = State()
    waiting_token = State()


# ── Step 1: Welcome + niche ───────────────────────────────────────────────────

async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 <b>Ласкаво просимо до Arete!</b>\n\n"
        "Я допоможу вам створити власного Telegram-бота для вашого бізнесу за 2 хвилини.\n\n"
        "Оберіть нішу:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=label, callback_data=f"niche:{niche.value}")]
                for niche, label in NICHE_LABELS.items()
            ]
        ),
    )
    await state.set_state(OnboardingFSM.select_niche)


# ── Step 2: Niche selected → show terms + naming rules ───────────────────────

async def got_niche(callback: types.CallbackQuery, state: FSMContext) -> None:
    niche_value = callback.data.split(":", 1)[1]
    niche = BotNiche(niche_value)
    await state.update_data(niche=niche_value)
    label = NICHE_LABELS[niche]
    example = NICHE_NAME_EXAMPLES[niche]

    await callback.message.edit_text(
        f"✅ Обрано: <b>{label}</b>\n\n"
        f"{NAMING_RULES}\n\n"
        "─────────────────────\n\n"
        "<b>Умови використання платформи:</b>\n\n"
        "• Ви надаєте технічний доступ до свого бота для роботи сервісу\n"
        "• Платформа не читає і не зберігає переписку ваших користувачів\n"
        "• Токен зберігається у зашифрованому вигляді\n"
        "• Ви можете відключити бота у будь-який момент\n"
        "• Сервіс надається на умовах підписки\n\n"
        f"<i>Приклад назви для вашої ніші: <code>{example}</code></i>",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="✅ Розумію та погоджуюсь",
                        callback_data="master:terms:agree",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="◀️ Змінити нішу",
                        callback_data="master:terms:back",
                    )
                ],
            ]
        ),
    )
    await state.set_state(OnboardingFSM.terms)
    await callback.answer()


# ── Step 3: Terms accepted → token instruction ────────────────────────────────

async def terms_agree(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    niche = BotNiche(data["niche"])
    example = NICHE_NAME_EXAMPLES[niche]

    await callback.message.edit_text(
        "✅ <b>Умови прийнято!</b>\n\n"
        "<b>Тепер створіть бота через @BotFather:</b>\n\n"
        "1️⃣ Відкрийте @BotFather\n"
        "2️⃣ Надішліть <code>/newbot</code>\n"
        "3️⃣ Введіть назву (відображається в профілі)\n"
        f"4️⃣ Введіть username — рекомендуємо: <code>{example}</code>\n"
        "5️⃣ Скопіюйте токен і надішліть сюди 👇",
    )
    await state.set_state(OnboardingFSM.waiting_token)
    await callback.answer()


async def terms_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Оберіть нішу:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=label, callback_data=f"niche:{niche.value}")]
                for niche, label in NICHE_LABELS.items()
            ]
        ),
    )
    await state.set_state(OnboardingFSM.select_niche)
    await callback.answer()


# ── Step 4: Receive and validate token ───────────────────────────────────────

async def got_token(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    plain_token = message.text.strip()

    if ":" not in plain_token or len(plain_token) < 30:
        await message.answer(
            "❌ Це не схоже на токен.\n"
            "Токен виглядає так: <code>1234567890:AAHvZ...</code>\n\n"
            "Скопіюйте точно з @BotFather і надішліть ще раз.",
        )
        return

    existing = await session.execute(
        select(RegisteredBot).where(RegisteredBot.token_hash == hash_token(plain_token))
    )
    if existing.scalar_one_or_none():
        await message.answer(
            "❌ Цей токен вже зареєстрований.\n"
            "Надішліть токен іншого бота або зверніться до підтримки."
        )
        return

    try:
        temp_bot = Bot(
            token=plain_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        bot_info = await temp_bot.get_me()
        await temp_bot.session.close()
    except TelegramUnauthorizedError:
        await message.answer(
            "❌ <b>Токен недійсний.</b>\n\n"
            "Перевірте що скопіювали повністю з @BotFather і надішліть ще раз.",
        )
        return
    except Exception:
        logger.exception("Token validation failed for user %s", message.from_user.id)
        await message.answer("❌ Не вдалося перевірити токен. Спробуйте ще раз.")
        return

    data = await state.get_data()
    niche = BotNiche(data["niche"])

    await register_bot(
        session,
        owner_telegram_id=message.from_user.id,
        plain_token=plain_token,
        bot_username=bot_info.username,
        niche=niche,
    )

    try:
        webhook_bot = Bot(token=plain_token)
        await webhook_bot.set_webhook(
            url=f"{settings.BASE_WEBHOOK_URL}/webhook/{plain_token}",
            secret_token=settings.SECRET_WEBHOOK_TOKEN,
            allowed_updates=["message", "callback_query"],
        )
        master_tag = f"@{app_state.master_bot_username}" if app_state.master_bot_username else "Arete"
        await webhook_bot.set_my_description(
            f"Цей бот створено та підтримується через {master_tag}.\n\nНіша: {NICHE_LABELS[niche]}",
            language_code="uk",
        )
        await webhook_bot.set_my_short_description(
            f"Powered by {master_tag}", language_code="uk",
        )
        await webhook_bot.session.close()
    except Exception:
        logger.exception("Failed to configure @%s", bot_info.username)
        await message.answer(
            "⚠️ Бот зареєстровано, але webhook не налаштувався. Зверніться до підтримки."
        )
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"🎉 <b>Ваш бот готовий!</b>\n\n"
        f"🤖 @{bot_info.username}\n"
        f"📦 Ніша: {NICHE_LABELS[niche]}\n\n"
        f"👉 Відкрийте та протестуйте: t.me/{bot_info.username}\n\n"
        "Ваші клієнти можуть користуватись ботом прямо зараз!",
    )


def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, CommandStart())
    dp.callback_query.register(got_niche,     F.data.startswith("niche:"),         OnboardingFSM.select_niche)
    dp.callback_query.register(terms_agree,   F.data == "master:terms:agree",      OnboardingFSM.terms)
    dp.callback_query.register(terms_back,    F.data == "master:terms:back",       OnboardingFSM.terms)
    dp.message.register(got_token, OnboardingFSM.waiting_token)
