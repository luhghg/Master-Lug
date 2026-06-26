"""
Platform owner admin panel inside the master bot.
Only accessible to PLATFORM_OWNER_ID.
"""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import niche_price, settings
from app.models.bot import BotNiche, RegisteredBot
from app.models.tattoo import TattooBooking, TattooReview, BookingStatus, ReviewStatus

logger = logging.getLogger(__name__)

NICHE_EMOJI = {
    BotNiche.LABOR:  "👷",
    BotNiche.BEAUTY: "🎨",
    BotNiche.SPORTS: "🏋️",
}

PRODUCT_NAMES = {
    BotNiche.BEAUTY: "Бот для майстра краси",
    BotNiche.LABOR:  "Бот для роботодавця",
}


class PlatformBroadcastFSM(StatesGroup):
    message = State()
    confirm = State()


def _is_owner(user_id: int) -> bool:
    return user_id == settings.PLATFORM_OWNER_ID


def _panel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="🤖 Всі боти",          callback_data="pa:bots:0"),
            types.InlineKeyboardButton(text="💳 Очікують оплати",    callback_data="pa:pending"),
        ],
        [
            types.InlineKeyboardButton(text="📊 Аналітика",          callback_data="pa:stats"),
            types.InlineKeyboardButton(text="📤 Реферали",           callback_data="pa:referrals:0"),
        ],
        [
            types.InlineKeyboardButton(text="📣 Розсилка",           callback_data="pa:broadcast"),
        ],
    ])


# ── /admin → show panel ───────────────────────────────────────────────────────

async def owner_start(message: types.Message) -> None:
    await message.answer(
        "🛠 <b>MasterLug — Панель власника</b>",
        reply_markup=_panel_kb(),
    )


async def pa_home(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>MasterLug — Панель власника</b>",
        reply_markup=_panel_kb(),
    )
    await callback.answer()


# ── All bots (paginated) ──────────────────────────────────────────────────────

async def pa_bots(callback: types.CallbackQuery, session: AsyncSession) -> None:
    page = int(callback.data.split(":")[2])
    per_page = 10

    result = await session.execute(
        select(RegisteredBot)
        .order_by(RegisteredBot.created_at.desc())
        .offset(page * per_page)
        .limit(per_page + 1)
    )
    bots = list(result.scalars().all())
    has_next = len(bots) > per_page
    bots = bots[:per_page]

    total = (await session.execute(select(func.count(RegisteredBot.id)))).scalar_one()

    if not bots:
        await callback.message.edit_text(
            "😔 Ботів ще немає.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home"),
            ]]),
        )
        await callback.answer()
        return

    lines = []
    for b in bots:
        status = "🟢" if b.is_active else "🔴"
        emoji = NICHE_EMOJI.get(b.niche, "🤖")
        lines.append(
            f"{status} {emoji} @{b.bot_username}\n"
            f"    👤 <code>{b.owner_telegram_id}</code> · {b.created_at.strftime('%d.%m.%Y')}"
        )

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"pa:bots:{page - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{page + 1}", callback_data="pa:noop"))
    if has_next:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"pa:bots:{page + 1}"))

    rows = [[types.InlineKeyboardButton(text=f"🔍 @{b.bot_username}", callback_data=f"pa:bot:{b.id}")] for b in bots]
    rows.append(nav)
    rows.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home")])

    await callback.message.edit_text(
        f"🤖 <b>Зареєстровані боти</b> (всього: {total})\n\n" + "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ── Pending payments ──────────────────────────────────────────────────────────

async def pa_pending(callback: types.CallbackQuery, session: AsyncSession) -> None:
    demo_ids = [d for d in [settings.DEMO_BOT_LABOR_ID, settings.DEMO_BOT_BEAUTY_ID] if d]

    q = select(RegisteredBot).where(RegisteredBot.is_active == False)
    if demo_ids:
        q = q.where(RegisteredBot.id.not_in(demo_ids))
    q = q.order_by(RegisteredBot.created_at.desc()).limit(25)

    result = await session.execute(q)
    bots = list(result.scalars().all())

    if not bots:
        await callback.message.edit_text(
            "✅ Немає ботів, що очікують оплати.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home"),
            ]]),
        )
        await callback.answer()
        return

    rows = []
    for b in bots:
        emoji = NICHE_EMOJI.get(b.niche, "🤖")
        rows.append([types.InlineKeyboardButton(
            text=f"{emoji} @{b.bot_username} · {b.created_at.strftime('%d.%m')}",
            callback_data=f"pa:bot:{b.id}",
        )])
    rows.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home")])

    await callback.message.edit_text(
        f"💳 <b>Очікують оплати ({len(bots)})</b>\n\n"
        "Натисніть на бот → <b>+7 днів 🎁</b> для безкоштовного тріалу або <b>+30/60/90</b> для підписки.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ── Single bot detail ─────────────────────────────────────────────────────────

async def pa_bot_detail(callback: types.CallbackQuery, session: AsyncSession) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    extra = ""
    if bot.niche == BotNiche.BEAUTY:
        total_b = (await session.execute(
            select(func.count(TattooBooking.id)).where(TattooBooking.bot_id == bot_id)
        )).scalar_one()
        active_b = (await session.execute(
            select(func.count(TattooBooking.id)).where(
                TattooBooking.bot_id == bot_id,
                TattooBooking.status == BookingStatus.NEW,
            )
        )).scalar_one()
        approved_r = (await session.execute(
            select(func.count(TattooReview.id)).where(
                TattooReview.bot_id == bot_id,
                TattooReview.status == ReviewStatus.APPROVED,
            )
        )).scalar_one()
        extra = (
            f"\n\n📅 Записів: <b>{total_b}</b> (активних: {active_b})\n"
            f"⭐️ Відгуків: <b>{approved_r}</b>"
        )

    status_text = "🟢 Активний" if bot.is_active else "🔴 Вимкнений"
    toggle_text = "🔴 Вимкнути" if bot.is_active else "🟢 Увімкнути"

    now = datetime.now(timezone.utc)
    if bot.subscription_expires_at is None:
        sub_text = "♾ Безлімітна"
    elif bot.subscription_expires_at > now:
        days_left = (bot.subscription_expires_at - now).days
        sub_text = f"✅ до {bot.subscription_expires_at.strftime('%d.%m.%Y')} ({days_left} дн.)"
    else:
        sub_text = f"🔴 Прострочена ({bot.subscription_expires_at.strftime('%d.%m.%Y')})"

    product = PRODUCT_NAMES.get(bot.niche, bot.niche.value)
    emoji = NICHE_EMOJI.get(bot.niche, "🤖")

    # Build subscription buttons with "✅ Видано DD.MM" indicator on the last-used button
    def _sub_btn(label: str, days: int) -> types.InlineKeyboardButton:
        if bot.last_grant_days == days and bot.last_grant_at:
            label = f"✅ Видано {bot.last_grant_at.strftime('%d.%m')}"
        return types.InlineKeyboardButton(text=label, callback_data=f"pa:sub_extend:{bot_id}:{days}")

    await callback.message.edit_text(
        f"{emoji} <b>@{bot.bot_username}</b>\n\n"
        f"🆔 Bot ID (для .env): <code>{bot.id}</code>\n"
        f"📦 {product}\n"
        f"👤 Owner: <code>{bot.owner_telegram_id}</code>\n"
        f"📅 Зареєстровано: {bot.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"💳 Підписка: {sub_text}\n"
        f"Статус: {status_text}"
        f"{extra}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=toggle_text, callback_data=f"pa:toggle:{bot_id}")],
            [_sub_btn("+7 днів 🎁", 7)],
            [_sub_btn("+30 днів", 30), _sub_btn("+60 днів", 60), _sub_btn("+90 днів", 90)],
            [types.InlineKeyboardButton(text="💳 Надіслати запит на оплату", callback_data=f"pa:pay_request:{bot_id}")],
            [types.InlineKeyboardButton(text="🗑 Видалити бот",              callback_data=f"pa:delete_confirm:{bot_id}")],
            [
                types.InlineKeyboardButton(text="◀️ Всі боти", callback_data="pa:bots:0"),
                types.InlineKeyboardButton(text="🏠 Панель",   callback_data="pa:home"),
            ],
        ]),
    )
    await callback.answer()


# ── Delete bot (with confirmation) ───────────────────────────────────────────

async def pa_delete_confirm(callback: types.CallbackQuery, session: AsyncSession) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    await callback.message.edit_text(
        f"⚠️ <b>Підтвердіть видалення</b>\n\n"
        f"🤖 @{bot.bot_username}\n"
        f"👤 Owner: <code>{bot.owner_telegram_id}</code>\n\n"
        f"Це видалить бота та всі пов'язані дані <b>назавжди</b>.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="🗑 Так, видалити", callback_data=f"pa:delete_do:{bot_id}"),
                types.InlineKeyboardButton(text="❌ Скасувати",      callback_data=f"pa:bot:{bot_id}"),
            ],
        ]),
    )
    await callback.answer()


async def pa_delete_do(callback: types.CallbackQuery, session: AsyncSession) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    username = bot.bot_username
    await session.delete(bot)
    await session.commit()
    logger.info("Deleted bot @%s (id=%s) by platform owner", username, bot_id)

    await callback.answer(f"✅ @{username} видалено", show_alert=True)
    callback.data = "pa:bots:0"
    await pa_bots(callback, session)


# ── Send Monobank payment request ────────────────────────────────────────────

async def pa_pay_request(
    callback: types.CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    bot_id = int(callback.data.split(":")[2])
    reg_bot = await session.get(RegisteredBot, bot_id)
    if not reg_bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    card = settings.MONOBANK_CARD or "—"
    price = niche_price(reg_bot.niche)
    username = reg_bot.bot_username

    text = (
        f"💳 <b>Запит на оплату підписки</b>\n\n"
        f"🤖 Бот: @{username}\n"
        f"💰 Сума: <b>{price} грн</b>\n\n"
        f"Картка для оплати:\n"
        f"<code>{card}</code>\n\n"
        f"‼️ <b>Призначення платежу (обов'язково!):</b>\n"
        f"<code>MasterLug @{username}</code>\n\n"
        f"Після оплати бот буде активований автоматично протягом кількох хвилин."
    )
    try:
        await bot.send_message(chat_id=reg_bot.owner_telegram_id, text=text)
        await callback.answer("✅ Запит надіслано власнику бота", show_alert=True)
    except Exception as e:
        logger.warning("pa_pay_request: could not notify owner %s: %s", reg_bot.owner_telegram_id, e)
        await callback.answer("❌ Не вдалося надіслати повідомлення власнику", show_alert=True)


# ── Toggle active ─────────────────────────────────────────────────────────────

async def pa_toggle(callback: types.CallbackQuery, session: AsyncSession) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    bot.is_active = not bot.is_active
    await session.commit()
    state_str = "увімкнено 🟢" if bot.is_active else "вимкнено 🔴"
    await callback.answer(f"@{bot.bot_username} {state_str}", show_alert=True)

    callback.data = f"pa:bot:{bot_id}"
    await pa_bot_detail(callback, session)


# ── Extend subscription ───────────────────────────────────────────────────────

async def pa_sub_extend(
    callback: types.CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    parts = callback.data.split(":")
    bot_id = int(parts[2])
    days = int(parts[3]) if len(parts) > 3 else 30

    reg_bot = await session.get(RegisteredBot, bot_id)
    if not reg_bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    base = max(reg_bot.subscription_expires_at or now, now)
    reg_bot.subscription_expires_at = base + timedelta(days=days)
    reg_bot.is_active = True
    reg_bot.last_grant_at = now
    reg_bot.last_grant_days = days
    await session.commit()

    new_date = reg_bot.subscription_expires_at.strftime("%d.%m.%Y")
    await callback.answer(f"✅ +{days} днів. Підписка до {new_date}", show_alert=True)

    if days == 7:
        owner_msg = "✅ Ваш бот активовано на 7 днів безкоштовно. Спробуйте всі функції!"
    else:
        owner_msg = f"✅ Підписку продовжено на {days} днів. Дякуємо!\n\n📅 Активна до: <b>{new_date}</b>"

    try:
        await bot.send_message(chat_id=reg_bot.owner_telegram_id, text=owner_msg)
    except Exception:
        pass

    callback.data = f"pa:bot:{bot_id}"
    await pa_bot_detail(callback, session)


# ── Referral report ──────────────────────────────────────────────────────────

async def pa_referrals(callback: types.CallbackQuery, session: AsyncSession) -> None:
    page = int(callback.data.split(":")[2])
    per_page = 8

    demo_ids = [d for d in [settings.DEMO_BOT_LABOR_ID, settings.DEMO_BOT_BEAUTY_ID] if d]

    # Get all bots that have a referrer, grouped by referrer
    q = select(RegisteredBot).where(RegisteredBot.referred_by != None)
    if demo_ids:
        q = q.where(RegisteredBot.id.not_in(demo_ids))
    q = q.order_by(RegisteredBot.created_at.desc())

    result = await session.execute(q)
    all_referred = list(result.scalars().all())

    if not all_referred:
        await callback.message.edit_text(
            "📤 <b>Реферали</b>\n\nПоки що ніхто не залучив клієнтів через реферальне посилання.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home"),
            ]]),
        )
        await callback.answer()
        return

    # Group by referrer
    by_referrer: dict[int, list[RegisteredBot]] = {}
    for bot in all_referred:
        by_referrer.setdefault(bot.referred_by, []).append(bot)

    referrers = list(by_referrer.items())
    total = len(referrers)
    slice_ = referrers[page * per_page: (page + 1) * per_page]
    has_next = len(referrers) > (page + 1) * per_page

    from app.core.redis_client import get_redis
    redis = await get_redis()

    lines = []
    for referrer_id, bots in slice_:
        clicks_raw = await redis.get(f"ref_clicks:{referrer_id}")
        clicks = int(clicks_raw) if clicks_raw else 0
        bot_list = ", ".join(f"@{b.bot_username}" for b in bots)
        lines.append(
            f"👤 <code>{referrer_id}</code>\n"
            f"  👆 Кліків: {clicks} · 🤖 Куплено: {len(bots)}\n"
            f"  {bot_list}"
        )

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"pa:referrals:{page - 1}"))
    if has_next:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"pa:referrals:{page + 1}"))

    rows = [nav] if nav else []
    rows.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home")])

    await callback.message.edit_text(
        f"📤 <b>Реферальна статистика</b> (рефереров: {total})\n\n"
        + "\n\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ── Platform stats ────────────────────────────────────────────────────────────

async def pa_stats(callback: types.CallbackQuery, session: AsyncSession) -> None:
    total_bots = (await session.execute(select(func.count(RegisteredBot.id)))).scalar_one()
    active_bots = (await session.execute(
        select(func.count(RegisteredBot.id)).where(RegisteredBot.is_active == True)
    )).scalar_one()
    pending_bots = total_bots - active_bots

    by_niche = await session.execute(
        select(RegisteredBot.niche, func.count(RegisteredBot.id).label("cnt"))
        .group_by(RegisteredBot.niche)
    )

    total_bookings = (await session.execute(select(func.count(TattooBooking.id)))).scalar_one()
    active_bookings = (await session.execute(
        select(func.count(TattooBooking.id)).where(TattooBooking.status == BookingStatus.NEW)
    )).scalar_one()
    total_reviews = (await session.execute(
        select(func.count(TattooReview.id)).where(TattooReview.status == ReviewStatus.APPROVED)
    )).scalar_one()

    niche_lines = "\n".join(
        f"  {NICHE_EMOJI.get(row.niche, '🤖')} {PRODUCT_NAMES.get(row.niche, row.niche.value)}: {row.cnt}"
        for row in by_niche.all()
    ) or "  —"

    monthly_revenue = active_bots * settings.SUBSCRIPTION_PRICE  # rough estimate

    await callback.message.edit_text(
        f"📊 <b>MasterLug — Аналітика</b>\n\n"
        f"🤖 Ботів всього: <b>{total_bots}</b>\n"
        f"  🟢 Активних: {active_bots} · 🔴 Очікують: {pending_bots}\n\n"
        f"По типах:\n{niche_lines}\n\n"
        f"💰 Орієнтовна виручка: <b>{monthly_revenue} грн/міс</b>\n\n"
        f"📅 Записів (Beauty): <b>{total_bookings}</b> (нових: {active_bookings})\n"
        f"⭐️ Відгуків схвалено: <b>{total_reviews}</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home"),
        ]]),
    )
    await callback.answer()


# ── Platform Broadcast FSM ────────────────────────────────────────────────────

async def pa_broadcast_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "📣 <b>Платформна розсилка</b>\n\n"
        "Надішліть текст або фото з підписом для розсилки всім власникам ботів.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="❌ Скасувати", callback_data="pa:broadcast_cancel"),
        ]]),
    )
    await state.set_state(PlatformBroadcastFSM.message)
    await callback.answer()


async def pa_broadcast_got(message: types.Message, state: FSMContext) -> None:
    if message.photo:
        await state.update_data(
            pa_broadcast_photo=message.photo[-1].file_id,
            pa_broadcast_text=message.caption or "",
        )
    elif message.text:
        await state.update_data(pa_broadcast_photo=None, pa_broadcast_text=message.text)
    else:
        await message.answer("❌ Підтримуються лише текст або фото з підписом.")
        return

    data = await state.get_data()
    preview = (data["pa_broadcast_text"] or "(фото без підпису)")[:300]
    await message.answer(
        f"📋 <b>Попередній перегляд:</b>\n\n{preview}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="✅ Розіслати всім", callback_data="pa:broadcast_confirm"),
            types.InlineKeyboardButton(text="❌ Скасувати",      callback_data="pa:broadcast_cancel"),
        ]]),
    )
    await state.set_state(PlatformBroadcastFSM.confirm)


async def pa_broadcast_confirm(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
) -> None:
    data = await state.get_data()
    photo = data.get("pa_broadcast_photo")
    text = data.get("pa_broadcast_text", "")
    await state.clear()

    demo_ids = [d for d in [settings.DEMO_BOT_LABOR_ID, settings.DEMO_BOT_BEAUTY_ID] if d]
    query = select(RegisteredBot.owner_telegram_id).distinct()
    if demo_ids:
        query = query.where(RegisteredBot.id.not_in(demo_ids))
    result = await session.execute(query)
    recipients = [row[0] for row in result.all()]

    sent = failed = 0
    for tid in recipients:
        try:
            if photo:
                await bot.send_photo(chat_id=tid, photo=photo, caption=text or None)
            else:
                await bot.send_message(chat_id=tid, text=text)
            sent += 1
        except Exception:
            failed += 1

    await callback.message.edit_text(
        f"✅ <b>Розсилку завершено!</b>\n\n"
        f"📤 Надіслано: {sent}\n❌ Помилок: {failed}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home"),
        ]]),
    )
    await callback.answer()


async def pa_broadcast_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "❌ Розсилку скасовано.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home"),
        ]]),
    )
    await callback.answer()


# ── Registration ──────────────────────────────────────────────────────────────

def register(dp: Dispatcher) -> None:
    owner = F.from_user.id == settings.PLATFORM_OWNER_ID

    dp.message.register(owner_start, Command("admin"), owner)
    dp.callback_query.register(pa_home,       F.data == "pa:home",               owner)
    dp.callback_query.register(pa_bots,       F.data.startswith("pa:bots:"),     owner)
    dp.callback_query.register(pa_pending,    F.data == "pa:pending",            owner)
    dp.callback_query.register(pa_bot_detail, F.data.startswith("pa:bot:"),      owner)
    dp.callback_query.register(pa_toggle,     F.data.startswith("pa:toggle:"),   owner)
    dp.callback_query.register(pa_sub_extend,   F.data.startswith("pa:sub_extend:"),  owner)
    dp.callback_query.register(pa_pay_request,    F.data.startswith("pa:pay_request:"),    owner)
    dp.callback_query.register(pa_delete_confirm, F.data.startswith("pa:delete_confirm:"), owner)
    dp.callback_query.register(pa_delete_do,      F.data.startswith("pa:delete_do:"),      owner)
    dp.callback_query.register(pa_stats,          F.data == "pa:stats",                    owner)
    dp.callback_query.register(pa_referrals,  F.data.startswith("pa:referrals:"),    owner)
    dp.callback_query.register(lambda c: c.answer(), F.data == "pa:noop",        owner)

    dp.callback_query.register(pa_broadcast_start,   F.data == "pa:broadcast",         owner)
    dp.message.register(pa_broadcast_got,            PlatformBroadcastFSM.message,     owner)
    dp.callback_query.register(pa_broadcast_confirm, F.data == "pa:broadcast_confirm", PlatformBroadcastFSM.confirm, owner)
    dp.callback_query.register(pa_broadcast_cancel,  F.data == "pa:broadcast_cancel",  owner)
