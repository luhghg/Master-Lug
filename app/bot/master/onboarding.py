import logging
from datetime import datetime, timedelta, timezone

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
from app.core.config import niche_price, settings
from app.core.security import hash_token
from app.models.bot import BotNiche, RegisteredBot
from app.services.bot_service import register_bot

logger = logging.getLogger(__name__)

NICHE_LABELS: dict[BotNiche, str] = {
    BotNiche.LABOR:  "💼 Робота та підробіток",
    BotNiche.BEAUTY: "💅 Краса та тату",
    BotNiche.TATTOO: "🖤 Тату-майстер",
    BotNiche.SPORTS: "🏋️ Спорт та фітнес",
}

PRODUCT_NAMES: dict[BotNiche, str] = {
    BotNiche.BEAUTY: "🎨 Бот для майстра краси",
    BotNiche.TATTOO: "🖤 Бот для тату-майстра",
    BotNiche.LABOR:  "👷 Бот для роботодавця",
}

NICHE_NAME_EXAMPLES: dict[BotNiche, str] = {
    BotNiche.LABOR:  "vinnytsia_robota_vasyl_bot",
    BotNiche.BEAUTY: "kyiv_beauty_master_bot",
    BotNiche.TATTOO: "kyiv_tatu_olga_bot",
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _btn(text: str, *, cb: str = "", url: str = "") -> types.InlineKeyboardButton:
    if url:
        return types.InlineKeyboardButton(text=text, url=url)
    return types.InlineKeyboardButton(text=text, callback_data=cb)


def _kb(*rows) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=list(rows))


async def _safe_edit(message: types.Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except Exception:
        await message.answer(text, **kwargs)


# ── Welcome landing ───────────────────────────────────────────────────────────

def _welcome_text() -> str:
    return (
        "👋 <b>Ласкаво просимо до MasterLug!</b>\n\n"
        "Платформа Telegram-ботів для малого бізнесу.\n\n"
        "⚡️ Готовий бот за 2 хвилини\n"
        "🔧 Без програмування\n"
        "💰 299 грн/місяць за бот\n\n"
        "Що вас цікавить?"
    )


def _welcome_kb(has_bots: bool = False) -> types.InlineKeyboardMarkup:
    rows = [
        [_btn("🤖 Обрати свій бот", cb="land:biz_type")],
        [_btn("💰 Ціни та умови", cb="land:pricing"), _btn("❓ Як це працює", cb="land:howto")],
    ]
    if has_bots:
        rows.append([_btn("👤 Мій профіль", cb="profile:home")])
    if settings.SUPPORT_USERNAME:
        rows.append([_btn("💬 Підтримка", url=f"https://t.me/{settings.SUPPORT_USERNAME}")])
    return _kb(*rows)


async def land_home(callback: types.CallbackQuery, session: AsyncSession) -> None:
    existing = await session.execute(
        select(RegisteredBot).where(RegisteredBot.owner_telegram_id == callback.from_user.id)
    )
    has_bots = bool(existing.scalars().all())
    await _safe_edit(callback.message, _welcome_text(), reply_markup=_welcome_kb(has_bots=has_bots))
    await callback.answer()


# ── Biz type selector (before catalog) ────────────────────────────────────────

async def land_biz_type(callback: types.CallbackQuery) -> None:
    await _safe_edit(
        callback.message,
        "👥 <b>Для кого шукаємо бота?</b>\n\n"
        "Оберіть тип — щоб показати найкращий варіант саме для вас:",
        reply_markup=_kb(
            [_btn("👤 Для себе  (я один майстер / підприємець)", cb="biz_type:SOLO")],
            [_btn("🏢 Для студії / компанії  (є команда або кілька майстрів)", cb="biz_type:STUDIO")],
            [_btn("ℹ️ Яка різниця?", cb="biz_type_info")],
            [_btn("◀️ Назад", cb="land:home")],
        ),
    )
    await callback.answer()


async def biz_type_info(callback: types.CallbackQuery) -> None:
    await callback.answer(
        "👤 Сольник — ви один, самі ведете клієнтів і керуєте ботом.\n\n"
        "🏢 Студія — у вас команда: кілька майстрів, адміністратор керує всіма.\n\n"
        "💡 Якщо ви один — обирайте «Для себе».",
        show_alert=True,
    )


async def biz_type_picked(callback: types.CallbackQuery, state: FSMContext) -> None:
    biz_type = callback.data.split(":", 1)[1]   # SOLO or STUDIO
    await state.update_data(business_type=biz_type)
    await _safe_edit(
        callback.message,
        "🛒 <b>Каталог ботів</b>\n\nОберіть тип бота для вашого бізнесу:",
        reply_markup=_kb(
            [_btn("🖤 Бот для тату-майстра",  cb="land:tattoo")],
            [_btn("🎨 Бот для майстра краси", cb="land:beauty")],
            [_btn("👷 Бот для роботодавця",   cb="land:labor")],
            [_btn("◀️ Назад",                 cb="land:biz_type")],
        ),
    )
    await callback.answer()


# ── Catalog ───────────────────────────────────────────────────────────────────

async def land_catalog(callback: types.CallbackQuery) -> None:
    await _safe_edit(
        callback.message,
        "🛒 <b>Каталог ботів</b>\n\nОберіть тип бота для вашого бізнесу:",
        reply_markup=_kb(
            [_btn("🖤 Бот для тату-майстра",  cb="land:tattoo")],
            [_btn("🎨 Бот для майстра краси", cb="land:beauty")],
            [_btn("👷 Бот для роботодавця",   cb="land:labor")],
            [_btn("◀️ Назад",                 cb="land:biz_type")],
        ),
    )
    await callback.answer()


# ── Product pages ─────────────────────────────────────────────────────────────

async def land_tattoo(callback: types.CallbackQuery) -> None:
    rows = []
    if settings.DEMO_BOT_TATTOO:
        rows.append([_btn("🤖 Спробувати демо-бот", url=f"https://t.me/{settings.DEMO_BOT_TATTOO}")])
    rows.append([_btn("🚀 Підключити цей бот", cb="register:TATTOO")])
    rows.append([_btn("◀️ До каталогу", cb="land:catalog")])
    await _safe_edit(
        callback.message,
        "🖤 <b>Бот для тату-майстра</b>\n\n"
        "Повноцінна система запису для тату-майстрів — з депозитом, портфоліо та CRM-клієнтів.\n\n"
        "<b>Що вміє бот:</b>\n"
        "✅ Анкета запису: стиль, зона, розмір, референс-фото\n"
        "✅ Алергія та перекриття — питає автоматично\n"
        "✅ Вибір дати і часу з вашого розкладу\n"
        "✅ Депозит: клієнт надсилає скріншот, ви підтверджуєте\n"
        "✅ Портфоліо за стилями з фото\n"
        "✅ CRM-клієнтів: рейтинг, no-show, нотатки\n"
        "✅ Управління розкладом і вихідними\n\n"
        f"💰 <b>{settings.SUBSCRIPTION_PRICE} грн/місяць</b>\n\n"
        "<i>Клієнти записуються самостійно 24/7 — ви тільки підтверджуєте оплату.</i>",
        reply_markup=_kb(*rows),
    )
    await callback.answer()


async def land_beauty(callback: types.CallbackQuery) -> None:
    rows = []
    if settings.DEMO_BOT_BEAUTY:
        rows.append([_btn("🤖 Спробувати демо-бот", url=f"https://t.me/{settings.DEMO_BOT_BEAUTY}")])
    rows.append([_btn("🚀 Підключити цей бот", cb="register:BEAUTY")])
    rows.append([_btn("◀️ До каталогу", cb="land:catalog")])

    demo_note = (
        f"\n\n<i>💡 Демо-бот показаний на прикладі тату-майстра — це лише приклад.</i>"
        if settings.DEMO_BOT_BEAUTY else ""
    )
    await _safe_edit(
        callback.message,
        "🎨 <b>Бот для майстра краси</b>\n\n"
        "Підходить для <b>будь-якого майстра</b>: тату, манікюр, брови, вії, косметолог, перукар, масаж — і не тільки.\n"
        "Якщо у вас є портфоліо і ви приймаєте клієнтів на сеанси — цей бот для вас.\n\n"
        "<b>Що вміє бот:</b>\n"
        "✅ Онлайн-запис — клієнт обирає дату і час сам\n"
        "✅ Портфоліо з фото\n"
        "✅ Відгуки клієнтів після сеансу\n"
        "✅ Сповіщення про нові записи миттєво\n"
        "✅ Управління розкладом і слотами\n"
        "✅ Список послуг з цінами\n\n"
        f"💰 <b>{settings.SUBSCRIPTION_PRICE} грн/місяць</b>\n\n"
        f"<i>Клієнти записуються самі — ви тільки працюєте.</i>"
        f"{demo_note}",
        reply_markup=_kb(*rows),
    )
    await callback.answer()


async def land_labor(callback: types.CallbackQuery) -> None:
    rows = []
    if settings.DEMO_BOT_LABOR:
        rows.append([_btn("🤖 Спробувати демо-бот", url=f"https://t.me/{settings.DEMO_BOT_LABOR}")])
    rows.append([_btn("🚀 Підключити цей бот", cb="register:LABOR")])
    rows.append([_btn("◀️ До каталогу", cb="land:catalog")])

    await _safe_edit(
        callback.message,
        "👷 <b>Бот для роботодавця</b>\n\n"
        "Для роботодавців у будівництві, складах, промоціях, сервісі.\n\n"
        "<b>Що вміє бот:</b>\n"
        "✅ Публікація вакансій — місто, оплата, адреса, час\n"
        "✅ Кандидати відгукуються прямо в боті\n"
        "✅ Ви приймаєте або відхиляєте одним кліком\n"
        "✅ Рейтинг працівників — захист від недобросовісних\n"
        "✅ Архів вакансій та статистика\n\n"
        f"💰 <b>{settings.SUBSCRIPTION_PRICE_LABOR} грн/місяць</b>\n\n"
        "<i>Публікуйте вакансії — кандидати самі приходять.</i>",
        reply_markup=_kb(*rows),
    )
    await callback.answer()


# ── Pricing & How-to pages ────────────────────────────────────────────────────

async def land_pricing(callback: types.CallbackQuery) -> None:
    await _safe_edit(
        callback.message,
        "💰 <b>Ціни та умови</b>\n\n"
        "🎯 <b>Один план — все включено</b>\n\n"
        "         <b>299 грн / місяць за бот</b>\n\n"
        "✅ Необмежена кількість клієнтів\n"
        "✅ Всі функції платформи\n"
        "✅ Технічна підтримка\n"
        "✅ Оновлення безкоштовно\n\n"
        "📌 <b>Кожен бот — окрема підписка:</b>\n"
        "  1 бот  →  299 грн/міс\n"
        "  2 боти →  398 грн/міс\n\n"
        f"📌 Beauty бот — <b>{settings.SUBSCRIPTION_PRICE} грн/міс</b>\n"
        f"📌 Labor бот — <b>{settings.SUBSCRIPTION_PRICE_LABOR} грн/міс</b>\n\n"
        "🎁 <b>Перші 3 клієнти платформи отримують\n"
        "перший бот безкоштовно на 30 днів!</b>",
        reply_markup=_kb(
            [_btn("🚀 Отримати бот", cb="land:catalog")],
            [_btn("◀️ Назад",        cb="land:home")],
        ),
    )
    await callback.answer()


async def land_howto(callback: types.CallbackQuery) -> None:
    await _safe_edit(
        callback.message,
        "❓ <b>Як підключити свого бота?</b>\n\n"
        "<b>Всього 3 кроки — займе 2-3 хвилини:</b>\n\n"
        "1️⃣ <b>Оберіть тип бота</b>\n"
        "   Краса/тату або Роботодавець\n\n"
        "2️⃣ <b>Створіть бота в @BotFather</b>\n"
        "   • /newbot → введіть назву → введіть username\n"
        "   • Скопіюйте токен: <code>1234567:AAH...</code>\n\n"
        "3️⃣ <b>Вставте токен сюди</b>\n"
        "   Бот одразу активується і готовий!\n\n"
        "4️⃣ <b>Налаштуйте через адмін-панель</b>\n"
        "   Відкрийте свого нового бота → /start\n"
        "   Там є все: портфоліо, розклад, привітання\n\n"
        "<i>Є питання? Пишіть у підтримку — допоможемо!</i>",
        reply_markup=_kb(
            [_btn("🚀 Підключити бот", cb="land:catalog")],
            [_btn("◀️ Назад",          cb="land:home")],
        ),
    )
    await callback.answer()


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    command: CommandObject = None,
) -> None:
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
        # Track referral click in Redis
        from app.core.redis_client import get_redis
        redis = await get_redis()
        await redis.incr(f"ref_clicks:{referrer_id}")

    # Always show welcome landing — but add "Мій профіль" button for existing users
    existing = await session.execute(
        select(RegisteredBot).where(RegisteredBot.owner_telegram_id == message.from_user.id)
    )
    bots = list(existing.scalars().all())
    await message.answer(_welcome_text(), reply_markup=_welcome_kb(has_bots=bool(bots)))


# ── Registration from landing (niche pre-selected) ───────────────────────────

_BIZ_TYPE_LABELS = {
    "SOLO":   "👤 Сольник",
    "STUDIO": "🏢 Студія / Компанія",
}

_LAND_BACK = {
    "BEAUTY": "land:beauty",
    "LABOR":  "land:labor",
    "TATTOO": "land:tattoo",
}


async def connect_from_landing(callback: types.CallbackQuery, state: FSMContext) -> None:
    niche_value = callback.data.split(":", 1)[1]
    try:
        niche = BotNiche(niche_value)
    except ValueError:
        await callback.answer("Невідома ніша", show_alert=True)
        return

    await state.update_data(niche=niche_value)
    await _show_terms(callback.message, niche, edit=True)
    await state.set_state(OnboardingFSM.terms)
    await callback.answer()


async def reg_biz_info(callback: types.CallbackQuery) -> None:
    await callback.answer(
        "👤 Сольник — один майстер, один розклад, сам керує.\n\n"
        "🏢 Студія — кілька майстрів, адмін розподіляє клієнтів між ними.\n\n"
        "💡 Якщо ви один — обирайте «Сольник».",
        show_alert=True,
    )


async def reg_type_picked(callback: types.CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")   # reg_type:BEAUTY:SOLO
    niche_value = parts[1]
    biz_type = parts[2]
    try:
        niche = BotNiche(niche_value)
    except ValueError:
        await callback.answer("Помилка", show_alert=True)
        return

    await state.update_data(niche=niche_value, business_type=biz_type)
    await _show_terms(callback.message, niche, edit=True)
    await state.set_state(OnboardingFSM.terms)
    await callback.answer()


def _terms_text(niche: BotNiche) -> str:
    example = NICHE_NAME_EXAMPLES[niche]
    product = PRODUCT_NAMES.get(niche, NICHE_LABELS[niche])
    return (
        f"✅ Обрано: <b>{product}</b>\n\n"
        f"{NAMING_RULES}\n\n"
        "─────────────────────\n\n"
        "<b>Умови використання платформи:</b>\n\n"
        "• Ви надаєте технічний доступ до бота для роботи сервісу\n"
        "• Платформа не читає переписку ваших користувачів\n"
        "• Токен зберігається у зашифрованому вигляді\n"
        "• Ви можете відключити бота у будь-який момент\n"
        "• Сервіс надається на умовах підписки (299 грн/міс за бот)\n"
        "• В описі вашого бота буде автоматично вказано платформу\n\n"
        f"<i>Рекомендована назва: <code>{example}</code></i>"
    )


async def _show_terms(message: types.Message, niche: BotNiche, edit: bool = False) -> None:
    text = _terms_text(niche)
    kb = _kb(
        [_btn("✅ Розумію та погоджуюсь", cb="master:terms:agree")],
        [_btn("◀️ До каталогу",           cb="master:terms:back")],
    )
    if edit:
        await _safe_edit(message, text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ── Onboarding FSM ────────────────────────────────────────────────────────────

async def _show_niche_selector(message: types.Message, state: FSMContext) -> None:
    """Fallback niche selector (used if somehow in select_niche state)."""
    await _safe_edit(
        message,
        "Оберіть нішу для нового бота:",
        reply_markup=_kb(
            [_btn("🎨 Бот для майстра краси", cb="niche:BEAUTY")],
            [_btn("👷 Бот для роботодавця",   cb="niche:LABOR")],
            [_btn("◀️ Мій профіль",           cb="profile:home")],
        ),
    )
    await state.set_state(OnboardingFSM.select_niche)


async def got_niche(callback: types.CallbackQuery, state: FSMContext) -> None:
    niche_value = callback.data.split(":", 1)[1]
    niche = BotNiche(niche_value)
    await state.update_data(niche=niche_value)
    await _show_terms(callback.message, niche, edit=True)
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
    await state.clear()
    await land_catalog(callback)


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

    fsm_data = await state.get_data()
    niche = BotNiche(fsm_data["niche"])
    referrer_id = fsm_data.get("referrer_id")

    registered, is_trial = await register_bot(
        session,
        owner_telegram_id=message.from_user.id,
        plain_token=plain_token,
        bot_username=bot_info.username,
        niche=niche,
        referred_by=referrer_id,
    )

    try:
        webhook_bot = Bot(token=plain_token)
        await webhook_bot.set_webhook(
            url=f"{settings.BASE_WEBHOOK_URL}/webhook/{plain_token}",
            secret_token=settings.SECRET_WEBHOOK_TOKEN,
            allowed_updates=["message", "callback_query"],
        )
        master_tag = f"@{app_state.master_bot_username}" if app_state.master_bot_username else "MasterLug"
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
        registered.is_active = False
        await session.commit()
        await message.answer(
            "⚠️ Бот зареєстровано, але webhook не налаштувався. "
            "Бот тимчасово деактивовано — зверніться до підтримки."
        )
        await state.clear()
        return

    await state.clear()

    product = PRODUCT_NAMES.get(niche, NICHE_LABELS[niche])

    price = niche_price(niche)
    if is_trial:
        await message.answer(
            f"🎉 <b>Ваш бот готовий!</b>\n\n"
            f"🤖 @{bot_info.username}\n"
            f"📦 {product}\n\n"
            f"👉 Відкрийте та протестуйте: t.me/{bot_info.username}\n\n"
            f"🎁 <b>Перший місяць — безкоштовно!</b>\n"
            f"Після 30 днів: {price} грн/міс.\n"
            f"Ми нагадаємо за тиждень до кінця.",
        )
    else:
        card = settings.MONOBANK_CARD or "уточніть у підтримці"
        support_hint = f"\n❓ Питання? @{settings.SUPPORT_USERNAME}" if settings.SUPPORT_USERNAME else ""
        await message.answer(
            f"✅ <b>Бот @{bot_info.username} зареєстровано!</b>\n\n"
            f"📦 {product}\n\n"
            f"⏳ <b>Для активації необхідна оплата</b>\n\n"
            f"💳 <b>Monobank:</b> <code>{card}</code>\n"
            f"💰 Сума: <b>{price} грн/міс</b>\n\n"
            f"⚠️ <b>ОБОВ'ЯЗКОВО вкажіть призначення платежу:</b>\n"
            f"┌─────────────────────────┐\n"
            f"  <code>MasterLug @{bot_info.username}</code>\n"
            f"└─────────────────────────┘\n"
            f"👆 <b>Скопіюйте та вставте цей текст при переказі!</b>\n"
            f"<i>Без правильного призначення ми не зможемо знайти вашу оплату.</i>\n\n"
            f"✅ Бот активується <b>автоматично</b> одразу після оплати."
            f"{support_hint}",
        )

    # Onboarding guide
    support_line = f"\n\n<i>Є питання? @{settings.SUPPORT_USERNAME}</i>" if settings.SUPPORT_USERNAME else ""

    if niche == BotNiche.LABOR:
        guide_text = (
            "📋 <b>Покрокова інструкція для старту:</b>\n\n"
            f"1️⃣ Відкрийте @{bot_info.username} → /start → <b>Панель роботодавця</b>\n"
            f"2️⃣ Натисніть <b>➕ Нова вакансія</b> — заповніть місто, опис, оплату, час\n"
            f"3️⃣ Поділіться посиланням: <code>t.me/{bot_info.username}</code>\n"
            "4️⃣ Кандидати відгукуються → ви приймаєте одним кліком"
            f"{support_line}"
        )
    elif niche == BotNiche.BEAUTY:
        guide_text = (
            "📋 <b>Покрокова інструкція для старту:</b>\n\n"
            f"1️⃣ Відкрийте @{bot_info.username} → /start → <b>Адмін-панель</b>\n"
            "   ⚙️ Налаштування → 👋 Привітання — введіть свій текст\n"
            "2️⃣ Натисніть <b>➕ Додати роботу</b> — завантажте фото з описом\n"
            "3️⃣ ⚙️ → 🕐 Час роботи — налаштуйте слоти (10:00, 12:00...)\n"
            f"4️⃣ Поділіться посиланням: <code>t.me/{bot_info.username}</code>\n\n"
            "<i>Клієнти записуватимуться самі — вам тільки підтверджувати!</i>"
            f"{support_line}"
        )
    else:
        guide_text = None

    if guide_text:
        await message.answer(guide_text)

    # Notify platform owner
    if settings.PLATFORM_OWNER_ID:
        try:
            from app.bot.master.dispatcher import get_master_bot
            master_bot = await get_master_bot()
            await master_bot.send_message(
                chat_id=settings.PLATFORM_OWNER_ID,
                text=(
                    f"🆕 <b>Новий бот зареєстровано!</b>\n\n"
                    f"🤖 @{bot_info.username}\n"
                    f"📦 {product}\n"
                    f"👤 ID: <code>{message.from_user.id}</code> "
                    f"@{message.from_user.username or '—'} / {message.from_user.full_name}\n"
                    f"💳 {'🎁 Безкоштовний тріал 30 дн.' if is_trial else '⏳ Очікує оплати'}"
                ),
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(
                        text="✅ Активувати на 30 днів",
                        callback_data=f"pa:sub_extend:{registered.id}:30",
                    )],
                    [types.InlineKeyboardButton(
                        text="🔍 Деталі бота",
                        callback_data=f"pa:bot:{registered.id}",
                    )],
                ]),
            )
        except Exception:
            pass

    # Referral bonus
    if referrer_id:
        from app.core.database import AsyncSessionLocal
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


# ── My Profile ────────────────────────────────────────────────────────────────

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
        return "🔴 Прострочена"
    days = (bot.subscription_expires_at - now).days
    date_str = bot.subscription_expires_at.strftime("%d.%m.%Y")
    if days <= 7:
        return f"⚠️ до {date_str} ({days} дн.)"
    return f"🟢 до {date_str}"


def _profile_kb(bots: list, user_id: int) -> types.InlineKeyboardMarkup:
    rows = []
    for bot in bots:
        emoji = NICHE_EMOJI.get(bot.niche, "🤖")
        status = _sub_status(bot)
        rows.append([_btn(f"{emoji} @{bot.bot_username} — {status}", cb=f"profile:bot:{bot.id}")])
    rows.append([_btn("➕ Додати ще бота", cb="profile:new")])
    if app_state.master_bot_username and user_id:
        rows.append([_btn("📤 Запросити друга → +30 днів", cb="referral:info")])
    if settings.SUPPORT_USERNAME:
        rows.append([_btn("💬 Підтримка", url=f"https://t.me/{settings.SUPPORT_USERNAME}")])
    return _kb(*rows)


async def referral_info(callback: types.CallbackQuery, session: AsyncSession) -> None:
    user_id = callback.from_user.id
    if not app_state.master_bot_username:
        await callback.answer("Реферальна програма тимчасово недоступна", show_alert=True)
        return

    # Stats from DB — how many registered via this user's referral
    purchases = (await session.scalar(
        select(func.count(RegisteredBot.id)).where(RegisteredBot.referred_by == user_id)
    )) or 0

    # Stats from Redis — how many clicked the link
    from app.core.redis_client import get_redis
    redis = await get_redis()
    clicks_raw = await redis.get(f"ref_clicks:{user_id}")
    clicks = int(clicks_raw) if clicks_raw else 0

    bonus_days = purchases * 30

    ref_link = f"https://t.me/{app_state.master_bot_username}?start=ref_{user_id}"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Спробуй%20MasterLug%20%E2%80%94%20Telegram-боти%20для%20бізнесу%20від%20299%20грн%2Fміс"

    await _safe_edit(
        callback.message,
        "📤 <b>Реферальна програма</b>\n\n"
        "🎁 <b>Умови:</b>\n"
        "Коли друг підключить бота через ваше посилання "
        "— вам автоматично <b>+30 днів</b> до підписки безкоштовно.\n\n"
        "📌 Бонус зараховується одразу після реєстрації друга.\n"
        "📌 Кількість запрошених не обмежена.\n\n"
        f"📊 <b>Ваша статистика:</b>\n"
        f"👆 Переходів по посиланню: <b>{clicks}</b>\n"
        f"🤖 Зареєстрували бота: <b>{purchases}</b>\n"
        f"🎁 Нараховано бонусів: <b>+{bonus_days} днів</b>\n\n"
        f"🔗 <b>Ваше посилання:</b>\n<code>{ref_link}</code>\n\n"
        "<i>Натисніть посилання вище щоб скопіювати.</i>",
        reply_markup=_kb(
            [_btn("📨 Поділитись посиланням", url=share_url)],
            [_btn("◀️ Мій профіль", cb="profile:home")],
        ),
    )
    await callback.answer()


async def _show_my_bots(message: types.Message, bots: list, user_id: int = 0) -> None:
    await message.answer(
        "👤 <b>Мій профіль</b>\n\nВаші боти:",
        reply_markup=_profile_kb(bots, user_id),
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

    await _safe_edit(
        callback.message,
        "👤 <b>Мій профіль</b>\n\nВаші боти:",
        reply_markup=_profile_kb(bots, callback.from_user.id),
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
    product = PRODUCT_NAMES.get(bot.niche, NICHE_LABELS[bot.niche])

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
        f"📦 {product}\n"
        f"📅 Зареєстровано: {bot.created_at.strftime('%d.%m.%Y')}\n"
        f"💳 Підписка: {sub_line}\n"
        f"\n👥 Підписників: <b>{subs}</b>\n{stat_line}"
    )

    needs_payment = (
        not bot.is_active
        or (bot.subscription_expires_at and (bot.subscription_expires_at - now).days <= 7)
    )
    if needs_payment and settings.MONOBANK_CARD:
        text += (
            f"\n\n━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 <b>Monobank:</b> <code>{settings.MONOBANK_CARD}</code>\n"
            f"💰 Сума: <b>{niche_price(bot.niche)} грн/міс</b>\n\n"
            f"⚠️ <b>ОБОВ'ЯЗКОВО призначення платежу:</b>\n"
            f"┌─────────────────────────┐\n"
            f"  <code>MasterLug @{bot.bot_username}</code>\n"
            f"└─────────────────────────┘\n"
            f"👆 <b>Скопіюйте та вставте при переказі!</b>\n"
            f"✅ Бот активується <b>автоматично</b> одразу після оплати."
        )

    kb_rows = []
    if bot.is_active and sub_valid:
        kb_rows.append([_btn("⏸ Призупинити бот", cb=f"profile:pause:{bot_id}")])
    elif not bot.is_active:
        sub_not_expired = (
            bot.subscription_expires_at is None
            or bot.subscription_expires_at > now
        )
        if sub_not_expired:
            kb_rows.append([_btn("▶️ Увімкнути бот", cb=f"profile:resume:{bot_id}")])
    kb_rows.append([_btn("◀️ Мої боти", cb="profile:home")])

    await _safe_edit(callback.message, text, reply_markup=_kb(*kb_rows))
    await callback.answer()


async def profile_toggle_bot(callback: types.CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    action = parts[1]
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

    callback.data = f"profile:bot:{bot_id}"
    await profile_bot_detail(callback, session)


async def profile_new_bot(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await land_catalog(callback)


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
    dp.callback_query.register(land_home,     F.data == "land:home")
    dp.callback_query.register(land_biz_type, F.data == "land:biz_type")
    dp.callback_query.register(land_catalog,  F.data == "land:catalog")
    dp.callback_query.register(land_tattoo,   F.data == "land:tattoo")
    dp.callback_query.register(land_beauty,   F.data == "land:beauty")
    dp.callback_query.register(land_labor,    F.data == "land:labor")
    dp.callback_query.register(land_pricing,  F.data == "land:pricing")
    dp.callback_query.register(land_howto,    F.data == "land:howto")

    # Biz type selection (solo vs studio)
    dp.callback_query.register(biz_type_info,   F.data == "biz_type_info")
    dp.callback_query.register(biz_type_picked, F.data.startswith("biz_type:"))

    # Connect from landing (pre-selected niche → terms)
    dp.callback_query.register(connect_from_landing, F.data.startswith("register:"))
    dp.callback_query.register(reg_biz_info,         F.data.startswith("reg_biz_info:"))
    dp.callback_query.register(reg_type_picked,      F.data.startswith("reg_type:"))

    # Referral info page
    dp.callback_query.register(referral_info, F.data == "referral:info")

    # Owner utility: send video/doc → get file_id
    dp.message.register(
        _capture_file_id,
        F.from_user.id == settings.PLATFORM_OWNER_ID,
        F.video | F.document,
    )

    # Profile
    dp.callback_query.register(profile_home,       F.data == "profile:home")
    dp.callback_query.register(profile_bot_detail, F.data.startswith("profile:bot:"))
    dp.callback_query.register(profile_new_bot,    F.data == "profile:new")
    dp.callback_query.register(profile_toggle_bot, F.data.startswith("profile:pause:"))
    dp.callback_query.register(profile_toggle_bot, F.data.startswith("profile:resume:"))

    # Onboarding FSM
    dp.callback_query.register(got_niche,   F.data.startswith("niche:"),        OnboardingFSM.select_niche)
    dp.callback_query.register(terms_agree, F.data == "master:terms:agree",     OnboardingFSM.terms)
    dp.callback_query.register(terms_back,  F.data == "master:terms:back",      OnboardingFSM.terms)
    dp.message.register(got_token, OnboardingFSM.waiting_token)
