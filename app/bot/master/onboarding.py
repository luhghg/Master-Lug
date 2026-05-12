import logging

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_state
from app.core.config import settings
from app.core.security import hash_token
from app.models.bot import BotNiche, RegisteredBot
from app.models.whitelist import PlatformWhitelist
from app.services.bot_service import register_bot

logger = logging.getLogger(__name__)

NICHE_LABELS: dict[BotNiche, str] = {
    BotNiche.LABOR:  "💼 Робота та підробіток",
    BotNiche.BEAUTY: "💅 Краса та тату",
    BotNiche.SPORTS: "🏋️ Спорт та фітнес",
}

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


# ── Landing helpers ───────────────────────────────────────────────────────────

def _connect_btn() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="📩 Підключитись", callback_data="wl:request")

def _back_btn() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text="◀️ Назад", callback_data="land:home")

def _landing_home_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💼 Бот для найму",      callback_data="land:labor")],
        [types.InlineKeyboardButton(text="💅 Бот для тату/краси", callback_data="land:beauty")],
        [types.InlineKeyboardButton(text="💰 Ціни",               callback_data="land:pricing")],
        [_connect_btn()],
    ])

def _landing_home_text() -> str:
    return (
        "👋 <b>Ласкаво просимо до MasterLug!</b>\n\n"
        "Платформа для створення Telegram-ботів під ваш бізнес.\n\n"
        "⚡️ Готовий бот за 2 хвилини\n"
        "🔧 Без програмування\n"
        "💰 Від 199 грн/місяць\n\n"
        "Оберіть вашу нішу щоб побачити демо:"
    )

async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


# ── Landing pages ─────────────────────────────────────────────────────────────

async def land_home(callback: types.CallbackQuery) -> None:
    await _safe_edit(callback.message, _landing_home_text(), reply_markup=_landing_home_kb())
    await callback.answer()


async def land_labor(callback: types.CallbackQuery) -> None:
    rows = []
    if settings.DEMO_BOT_LABOR:
        rows.append([types.InlineKeyboardButton(
            text="🤖 Спробувати демо-бот",
            url=f"https://t.me/{settings.DEMO_BOT_LABOR}",
        )])
    rows.append([_back_btn(), _connect_btn()])

    await _safe_edit(
        callback.message,
        "💼 <b>Бот для найму персоналу</b>\n\n"
        "Для роботодавців у будівництві, складах, промоціях, "
        "сервісі — де потрібні разові або постійні працівники.\n\n"
        "<b>Що вміє бот:</b>\n"
        "✅ Публікація вакансій — місто, оплата, адреса, час\n"
        "✅ Кандидати відгукуються прямо в боті\n"
        "✅ Ви приймаєте або відхиляєте одним кліком\n"
        "✅ Рейтинг працівників — захист від недобросовісних\n"
        "✅ Архів вакансій та статистика по заявках\n\n"
        "<i>Роботодавець публікує — кандидати самі приходять.</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def land_beauty(callback: types.CallbackQuery) -> None:
    rows = []
    if settings.DEMO_BOT_BEAUTY:
        rows.append([types.InlineKeyboardButton(
            text="🤖 Спробувати демо-бот",
            url=f"https://t.me/{settings.DEMO_BOT_BEAUTY}",
        )])
    rows.append([_back_btn(), _connect_btn()])

    await _safe_edit(
        callback.message,
        "💅 <b>Бот для тату-майстрів та б'юті-індустрії</b>\n\n"
        "Для тату-майстрів, косметологів, нейл-майстрів, "
        "перукарів — будь-яких б'юті-спеціалістів.\n\n"
        "<b>Що вміє бот:</b>\n"
        "✅ Онлайн-запис — клієнт обирає дату і час сам\n"
        "✅ Портфоліо по стилях з фото\n"
        "✅ Відгуки клієнтів після сеансу\n"
        "✅ Миттєві сповіщення про нові записи\n"
        "✅ Управління розкладом і слотами\n\n"
        "<i>Клієнти записуються самі — ви тільки працюєте.</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


_VIDEO_MAP = {
    "labor_client":  lambda: settings.DEMO_VIDEO_LABOR_CLIENT,
    "labor_admin":   lambda: settings.DEMO_VIDEO_LABOR_ADMIN,
    "beauty_client": lambda: settings.DEMO_VIDEO_BEAUTY_CLIENT,
    "beauty_admin":  lambda: settings.DEMO_VIDEO_BEAUTY_ADMIN,
}

async def land_video(callback: types.CallbackQuery) -> None:
    key = callback.data.split(":")[2]
    getter = _VIDEO_MAP.get(key)
    file_id = getter() if getter else ""
    if not file_id:
        await callback.answer("Відео ще не додано", show_alert=True)
        return
    await callback.message.answer_video(
        video=file_id,
        caption="📹 Демо-відео платформи Arete",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="📩 Підключитись", callback_data="wl:request"),
        ]]),
    )
    await callback.answer()


async def land_pricing(callback: types.CallbackQuery) -> None:
    await _safe_edit(
        callback.message,
        "💰 <b>Ціни та умови</b>\n\n"
        "🎯 <b>Один план — все включено</b>\n\n"
        "       <b>199 грн / місяць</b>\n\n"
        "✅ Необмежена кількість клієнтів\n"
        "✅ Всі функції платформи\n"
        "✅ Технічна підтримка\n"
        "✅ Оновлення безкоштовно\n"
        "✅ Ваш особистий Telegram-бот\n\n"
        "🎁 <b>Перший місяць — безкоштовно</b>\n"
        "<i>(для перших 10 клієнтів)</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [_back_btn(), _connect_btn()],
        ]),
    )
    await callback.answer()


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(message: types.Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user_id = message.from_user.id

    if user_id != settings.PLATFORM_OWNER_ID:
        result = await session.execute(
            select(PlatformWhitelist).where(PlatformWhitelist.telegram_id == user_id)
        )
        if not result.scalar_one_or_none():
            await message.answer(_landing_home_text(), reply_markup=_landing_home_kb())
            return

    await message.answer(
        "👋 <b>Ласкаво просимо до MasterLug!</b>\n\n"
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


# ── Onboarding: niche → terms → token ────────────────────────────────────────

async def got_niche(callback: types.CallbackQuery, state: FSMContext) -> None:
    niche_value = callback.data.split(":", 1)[1]
    niche = BotNiche(niche_value)
    await state.update_data(niche=niche_value)
    example = NICHE_NAME_EXAMPLES[niche]

    await callback.message.edit_text(
        f"✅ Обрано: <b>{NICHE_LABELS[niche]}</b>\n\n"
        f"{NAMING_RULES}\n\n"
        "─────────────────────\n\n"
        "<b>Умови використання платформи:</b>\n\n"
        "• Ви надаєте технічний доступ до свого бота для роботи сервісу\n"
        "• Платформа не читає і не зберігає переписку ваших користувачів\n"
        "• Токен зберігається у зашифрованому вигляді\n"
        "• Ви можете відключити бота у будь-який момент\n"
        "• Сервіс надається на умовах підписки\n"
        "• <b>В описі вашого бота обов'язково має бути вказано посилання на платформу</b> — це буде встановлено автоматично\n\n"
        f"<i>Приклад назви для вашої ніші: <code>{example}</code></i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Розумію та погоджуюсь", callback_data="master:terms:agree")],
            [types.InlineKeyboardButton(text="◀️ Змінити нішу",          callback_data="master:terms:back")],
        ]),
    )
    await state.set_state(OnboardingFSM.terms)
    await callback.answer()


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
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=label, callback_data=f"niche:{niche.value}")]
            for niche, label in NICHE_LABELS.items()
        ]),
    )
    await state.set_state(OnboardingFSM.select_niche)
    await callback.answer()


# ── Token validation & bot registration ──────────────────────────────────────

async def got_token(message: types.Message, state: FSMContext, session: AsyncSession) -> None:
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
        temp_bot = Bot(token=plain_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
        await webhook_bot.set_my_short_description(f"Powered by {master_tag}", language_code="uk")
        await webhook_bot.set_my_commands([
            types.BotCommand(command="start", description="🏠 Головне меню"),
            types.BotCommand(command="menu",  description="📋 Відкрити меню"),
            types.BotCommand(command="back",  description="◀️ Повернутись до меню"),
        ])
        await webhook_bot.session.close()
    except Exception:
        logger.exception("Failed to configure @%s", bot_info.username)
        await message.answer("⚠️ Бот зареєстровано, але webhook не налаштувався. Зверніться до підтримки.")
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


# ── Whitelist request (from landing) ─────────────────────────────────────────

async def whitelist_request(callback: types.CallbackQuery, bot: Bot) -> None:
    user = callback.from_user
    mention = f"@{user.username}" if user.username else user.full_name
    await _safe_edit(
        callback.message,
        "✅ <b>Заявку надіслано!</b>\n\n"
        "Очікуйте підтвердження від адміністратора.\n"
        "Зазвичай відповідаємо протягом кількох годин.",
    )
    try:
        await bot.send_message(
            chat_id=settings.PLATFORM_OWNER_ID,
            text=(
                f"📩 <b>Нова заявка на підключення</b>\n\n"
                f"👤 {mention}\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"Ім'я: {user.full_name}"
            ),
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="✅ Дозволити", callback_data=f"wl:approve:{user.id}:{user.full_name[:30]}"),
                types.InlineKeyboardButton(text="❌ Відхилити", callback_data=f"wl:decline:{user.id}"),
            ]]),
        )
    except Exception:
        logger.warning("Could not notify owner about whitelist request")
    await callback.answer()


# ── Owner utility: capture file_id ───────────────────────────────────────────

async def _capture_file_id(message: types.Message) -> None:
    file_id = message.video.file_id if message.video else message.document.file_id
    await message.reply(
        f"📋 <b>file_id:</b>\n\n"
        f"<code>{file_id}</code>\n\n"
        f"Встав у <code>.env</code> потрібний рядок:\n\n"
        f"<code>DEMO_VIDEO_LABOR_CLIENT={file_id}</code>\n"
        f"<code>DEMO_VIDEO_LABOR_ADMIN={file_id}</code>\n"
        f"<code>DEMO_VIDEO_BEAUTY_CLIENT={file_id}</code>\n"
        f"<code>DEMO_VIDEO_BEAUTY_ADMIN={file_id}</code>"
    )


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_start, Command("menu"))
    dp.message.register(cmd_start, Command("back"))

    # Landing navigation
    dp.callback_query.register(land_home,    F.data == "land:home")
    dp.callback_query.register(land_labor,   F.data == "land:labor")
    dp.callback_query.register(land_beauty,  F.data == "land:beauty")
    dp.callback_query.register(land_pricing, F.data == "land:pricing")
    dp.callback_query.register(land_video,   F.data.startswith("land:video:"))

    # Owner utility: send any video to master bot → get file_id back
    dp.message.register(
        _capture_file_id,
        F.from_user.id == settings.PLATFORM_OWNER_ID,
        F.video | F.document,
    )

    # Whitelist
    dp.callback_query.register(whitelist_request, F.data == "wl:request")

    # Onboarding FSM
    dp.callback_query.register(got_niche,   F.data.startswith("niche:"),       OnboardingFSM.select_niche)
    dp.callback_query.register(terms_agree, F.data == "master:terms:agree",    OnboardingFSM.terms)
    dp.callback_query.register(terms_back,  F.data == "master:terms:back",     OnboardingFSM.terms)
    dp.message.register(got_token, OnboardingFSM.waiting_token)
