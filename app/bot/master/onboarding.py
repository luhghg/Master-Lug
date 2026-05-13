import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
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
        "<i>(для перших 3 клієнтів)</i>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [_back_btn(), _connect_btn()],
        ]),
    )
    await callback.answer()


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    command: CommandObject = None,
) -> None:
    # Parse referral before clearing state
    args = (command.args or "") if command else ""
    referrer_id = None
    if args.startswith("ref_"):
        try:
            rid = int(args[4:])
            if rid != message.from_user.id:
                referrer_id = rid
        except ValueError:
            pass

    await state.clear()

    if referrer_id:
        await state.update_data(referrer_id=referrer_id)

    user_id = message.from_user.id

    if user_id != settings.PLATFORM_OWNER_ID:
        result = await session.execute(
            select(PlatformWhitelist).where(PlatformWhitelist.telegram_id == user_id)
        )
        if not result.scalar_one_or_none():
            await message.answer(_landing_home_text(), reply_markup=_landing_home_kb())
            return

    # If user already has bots → show profile + option to add more
    existing = await session.execute(
        select(RegisteredBot).where(RegisteredBot.owner_telegram_id == user_id)
    )
    bots = list(existing.scalars().all())
    if bots:
        await _show_my_bots(message, bots, user_id)
        return

    await _show_niche_selector(message, state)


async def _show_niche_selector(message: types.Message, state: FSMContext) -> None:
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

    # Get FSM data (including referrer_id) BEFORE clearing state
    fsm_data = await state.get_data()
    niche = BotNiche(fsm_data["niche"])
    referrer_id = fsm_data.get("referrer_id")

    registered, is_trial = await register_bot(
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
    if is_trial:
        await message.answer(
            f"🎉 <b>Ваш бот готовий!</b>\n\n"
            f"🤖 @{bot_info.username}\n"
            f"📦 Ніша: {NICHE_LABELS[niche]}\n\n"
            f"👉 Відкрийте та протестуйте: t.me/{bot_info.username}\n\n"
            f"🎁 <b>Перший місяць — безкоштовно!</b>\n"
            f"Після 30 днів підписка коштує {settings.SUBSCRIPTION_PRICE} грн/міс.\n"
            f"Ми нагадаємо за тиждень до кінця.",
        )
    else:
        card = settings.MONOBANK_CARD or "уточніть у підтримці"
        await message.answer(
            f"✅ <b>Бот @{bot_info.username} зареєстровано!</b>\n\n"
            f"📦 Ніша: {NICHE_LABELS[niche]}\n\n"
            f"⏳ <b>Для активації необхідна оплата</b>\n"
            f"Вартість: <b>{settings.SUBSCRIPTION_PRICE} грн/міс</b>\n\n"
            f"💳 Monobank: <code>{card}</code>\n"
            f"📝 Призначення: <code>MasterLug @{bot_info.username}</code>\n\n"
            f"Після оплати напишіть нам — активуємо протягом кількох годин.",
        )

    # Feature 3: Send step-by-step onboarding guide
    support_line = ""
    if settings.SUPPORT_USERNAME:
        support_line = f"\n\n<i>Є питання? Пишіть у підтримку @{settings.SUPPORT_USERNAME}</i>"

    if niche == BotNiche.LABOR:
        guide_text = (
            "📋 <b>Покрокова інструкція для старту:</b>\n\n"
            f"1️⃣ Відкрийте @{bot_info.username} → /start → <b>Панель роботодавця</b>\n"
            f"2️⃣ Натисніть <b>➕ Нова вакансія</b> — заповніть місто, опис, оплату, час і адресу\n"
            f"3️⃣ Поділіться посиланням з кандидатами: <code>t.me/{bot_info.username}</code>\n"
            "4️⃣ Кандидати відгукуються → ви приймаєте одним кліком"
            f"{support_line}"
        )
    elif niche == BotNiche.BEAUTY:
        guide_text = (
            "📋 <b>Покрокова інструкція для старту:</b>\n\n"
            f"1️⃣ Відкрийте @{bot_info.username} → /start → <b>Адмін-панель</b>\n"
            "   ⚙️ Налаштування → 👋 Привітання — введіть свій текст\n"
            "2️⃣ Натисніть <b>➕ Додати роботу</b> — завантажте фото з описом і ціною\n"
            "3️⃣ ⚙️ → 🕐 Час роботи — налаштуйте слоти (10:00, 12:00...)\n"
            f"4️⃣ Поділіться посиланням: <code>t.me/{bot_info.username}</code>\n\n"
            "<i>Клієнти записуватимуться самі — вам тільки підтверджувати!</i>"
        )
    else:
        guide_text = None

    if guide_text:
        await message.answer(guide_text)

    # Notify platform owner about new registration
    if settings.PLATFORM_OWNER_ID:
        try:
            from app.bot.master.dispatcher import get_master_bot
            master_bot = await get_master_bot()
            await master_bot.send_message(
                chat_id=settings.PLATFORM_OWNER_ID,
                text=(
                    f"🆕 <b>Новий бот зареєстровано!</b>\n\n"
                    f"🤖 @{bot_info.username}\n"
                    f"📦 Ніша: {NICHE_LABELS[niche]}\n"
                    f"👤 Owner ID: <code>{message.from_user.id}</code>\n"
                    f"👤 @{message.from_user.username or '—'} / {message.from_user.full_name}"
                ),
            )
        except Exception:
            pass

    # Feature 8: Referral bonus
    if referrer_id:
        from app.core.database import AsyncSessionLocal
        from datetime import timedelta
        async with AsyncSessionLocal() as ref_session:
            ref_bots_res = await ref_session.execute(
                select(RegisteredBot).where(
                    RegisteredBot.owner_telegram_id == referrer_id
                ).limit(1)
            )
            ref_bot = ref_bots_res.scalar_one_or_none()
            if ref_bot:
                now_utc = datetime.now(timezone.utc)
                base = max(ref_bot.subscription_expires_at or now_utc, now_utc)
                ref_bot.subscription_expires_at = base + timedelta(days=30)
                await ref_session.commit()
                try:
                    from app.bot.master.dispatcher import get_master_bot
                    master_bot = await get_master_bot()
                    await master_bot.send_message(
                        chat_id=referrer_id,
                        text="🎁 <b>Ваш друг зареєструвався!</b>\n\nВам нараховано +30 днів до підписки.",
                    )
                except Exception:
                    pass


# ── My Profile ───────────────────────────────────────────────────────────────

NICHE_EMOJI = {
    BotNiche.LABOR:  "💼",
    BotNiche.BEAUTY: "💅",
    BotNiche.SPORTS: "🏋️",
}


def _sub_status(bot: RegisteredBot) -> str:
    if not bot.is_active:
        return "🔴 Вимкнений"
    if bot.subscription_expires_at is None:
        return "🟢 Активний"
    now = datetime.now(timezone.utc)
    if bot.subscription_expires_at <= now:
        return "🔴 Підписка прострочена"
    days = (bot.subscription_expires_at - now).days
    date_str = bot.subscription_expires_at.strftime("%d.%m.%Y")
    if days <= 7:
        return f"⚠️ до {date_str} ({days} дн.)"
    return f"🟢 до {date_str}"


async def _show_my_bots(message: types.Message, bots: list, user_id: int = 0) -> None:
    rows = []
    for bot in bots:
        emoji = NICHE_EMOJI.get(bot.niche, "🤖")
        status = _sub_status(bot)
        rows.append([types.InlineKeyboardButton(
            text=f"{emoji} @{bot.bot_username} — {status}",
            callback_data=f"profile:bot:{bot.id}",
        )])
    rows.append([types.InlineKeyboardButton(text="➕ Додати ще бота", callback_data="profile:new")])

    # Feature 8: Referral button
    if app_state.master_bot_username and user_id:
        ref_link = f"https://t.me/{app_state.master_bot_username}?start=ref_{user_id}"
        rows.append([types.InlineKeyboardButton(
            text="📤 Запросити друга → +30 днів",
            url=ref_link,
        )])

    # Feature 2: Support button
    if settings.SUPPORT_USERNAME:
        rows.append([types.InlineKeyboardButton(
            text="💬 Підтримка",
            url=f"https://t.me/{settings.SUPPORT_USERNAME}",
        )])

    await message.answer(
        "👤 <b>Мій профіль</b>\n\nВаші боти:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def profile_home(callback: types.CallbackQuery, session: AsyncSession) -> None:
    bots_res = await session.execute(
        select(RegisteredBot).where(RegisteredBot.owner_telegram_id == callback.from_user.id)
    )
    bots = list(bots_res.scalars().all())
    if not bots:
        await _safe_edit(callback.message, "У вас ще немає ботів.")
        await callback.answer()
        return

    rows = []
    for bot in bots:
        emoji = NICHE_EMOJI.get(bot.niche, "🤖")
        status = _sub_status(bot)
        rows.append([types.InlineKeyboardButton(
            text=f"{emoji} @{bot.bot_username} — {status}",
            callback_data=f"profile:bot:{bot.id}",
        )])
    rows.append([types.InlineKeyboardButton(text="➕ Додати ще бота", callback_data="profile:new")])

    # Feature 8: Referral button
    user_id = callback.from_user.id
    if app_state.master_bot_username:
        ref_link = f"https://t.me/{app_state.master_bot_username}?start=ref_{user_id}"
        rows.append([types.InlineKeyboardButton(
            text="📤 Запросити друга → +30 днів",
            url=ref_link,
        )])

    # Feature 2: Support button
    if settings.SUPPORT_USERNAME:
        rows.append([types.InlineKeyboardButton(
            text="💬 Підтримка",
            url=f"https://t.me/{settings.SUPPORT_USERNAME}",
        )])

    await _safe_edit(
        callback.message,
        "👤 <b>Мій профіль</b>\n\nВаші боти:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def profile_bot_detail(callback: types.CallbackQuery, session: AsyncSession) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)

    if not bot or bot.owner_telegram_id != callback.from_user.id:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    emoji = NICHE_EMOJI.get(bot.niche, "🤖")

    sub_valid = bot.is_active and (
        bot.subscription_expires_at is None or bot.subscription_expires_at > now
    )

    if not bot.is_active:
        sub_line = "🔴 <b>Вимкнений / не оплачено</b>"
    elif bot.subscription_expires_at is None:
        sub_line = "🟢 <b>Активний</b>"
    elif bot.subscription_expires_at <= now:
        sub_line = f"🔴 <b>Підписка прострочена</b> ({bot.subscription_expires_at.strftime('%d.%m.%Y')})"
    else:
        days = (bot.subscription_expires_at - now).days
        date_str = bot.subscription_expires_at.strftime("%d.%m.%Y")
        sub_line = f"🟢 <b>Активний до {date_str}</b> ({days} дн.)"

    # Feature 7: Query stats
    from datetime import date
    from app.models.tattoo import BotSubscription, TattooBooking
    from app.models.job import Job

    first_of_month = datetime(date.today().year, date.today().month, 1, tzinfo=timezone.utc)

    subs = (await session.scalar(
        select(func.count(BotSubscription.id)).where(BotSubscription.bot_id == bot_id)
    )) or 0

    if bot.niche.value == "BEAUTY":
        month_stat = (await session.scalar(
            select(func.count(TattooBooking.id)).where(
                TattooBooking.bot_id == bot_id,
                TattooBooking.created_at >= first_of_month,
            )
        )) or 0
        stat_line = f"📅 Записів цього місяця: <b>{month_stat}</b>"
    elif bot.niche.value == "LABOR":
        month_stat = (await session.scalar(
            select(func.count(Job.id)).where(
                Job.bot_id == bot_id,
                Job.created_at >= first_of_month,
            )
        )) or 0
        stat_line = f"📋 Вакансій цього місяця: <b>{month_stat}</b>"
    else:
        stat_line = ""

    text = (
        f"{emoji} <b>@{bot.bot_username}</b>\n\n"
        f"📦 Ніша: {NICHE_LABELS[bot.niche]}\n"
        f"📅 Зареєстровано: {bot.created_at.strftime('%d.%m.%Y')}\n"
        f"💳 Підписка: {sub_line}\n"
        f"\n👥 Підписників: <b>{subs}</b>\n{stat_line}"
    )

    # Show payment instructions if not active or expiring soon
    needs_payment = (
        not bot.is_active
        or bot.subscription_expires_at is None and not bot.is_active
        or (bot.subscription_expires_at and (bot.subscription_expires_at - now).days <= 7)
    )
    if needs_payment and settings.MONOBANK_CARD:
        card = settings.MONOBANK_CARD
        text += (
            f"\n\n💳 <b>Оплата підписки:</b> {settings.SUBSCRIPTION_PRICE} грн/міс\n"
            f"Monobank: <code>{card}</code>\n"
            f"Призначення: <code>MasterLug @{bot.bot_username}</code>\n\n"
            f"<i>Після оплати напишіть нам — активуємо вручну.</i>"
        )

    # Feature 1: Build toggle button
    kb_rows = []
    if bot.is_active and sub_valid:
        kb_rows.append([types.InlineKeyboardButton(
            text="⏸ Призупинити бот",
            callback_data=f"profile:pause:{bot_id}",
        )])
    elif not bot.is_active:
        # Only show resume if subscription not expired
        sub_not_expired = (
            bot.subscription_expires_at is None
            or bot.subscription_expires_at > now
        )
        if sub_not_expired:
            kb_rows.append([types.InlineKeyboardButton(
                text="▶️ Увімкнути бот",
                callback_data=f"profile:resume:{bot_id}",
            )])

    kb_rows.append([types.InlineKeyboardButton(text="◀️ Мої боти", callback_data="profile:home")])
    kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _safe_edit(callback.message, text, reply_markup=kb)
    await callback.answer()


async def profile_toggle_bot(callback: types.CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    action = parts[1]  # "pause" or "resume"
    bot_id = int(parts[2])

    bot = await session.get(RegisteredBot, bot_id)
    if not bot or bot.owner_telegram_id != callback.from_user.id:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    now = datetime.now(timezone.utc)

    if action == "pause":
        bot.is_active = False
        await session.commit()
        await callback.answer("⏸ Бот призупинено", show_alert=True)
    elif action == "resume":
        # Check subscription is still valid
        sub_valid = (
            bot.subscription_expires_at is None
            or bot.subscription_expires_at > now
        )
        if not sub_valid:
            await callback.answer("❌ Підписка прострочена. Оновіть підписку для активації.", show_alert=True)
            return
        bot.is_active = True
        await session.commit()
        await callback.answer("▶️ Бот увімкнено", show_alert=True)

    # Refresh detail view
    callback.data = f"profile:bot:{bot_id}"
    await profile_bot_detail(callback, session)


async def profile_new_bot(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _show_niche_selector(callback.message, state)


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

    # Profile
    dp.callback_query.register(profile_home,       F.data == "profile:home")
    dp.callback_query.register(profile_bot_detail, F.data.startswith("profile:bot:"))
    dp.callback_query.register(profile_new_bot,    F.data == "profile:new")

    # Feature 1: Pause/Resume toggle
    dp.callback_query.register(profile_toggle_bot, F.data.startswith("profile:pause:"))
    dp.callback_query.register(profile_toggle_bot, F.data.startswith("profile:resume:"))

    # Whitelist
    dp.callback_query.register(whitelist_request, F.data == "wl:request")

    # Onboarding FSM
    dp.callback_query.register(got_niche,   F.data.startswith("niche:"),       OnboardingFSM.select_niche)
    dp.callback_query.register(terms_agree, F.data == "master:terms:agree",    OnboardingFSM.terms)
    dp.callback_query.register(terms_back,  F.data == "master:terms:back",     OnboardingFSM.terms)
    dp.message.register(got_token, OnboardingFSM.waiting_token)
