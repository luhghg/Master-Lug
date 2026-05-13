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

from app.core.config import settings
from app.models.bot import BotNiche, RegisteredBot
from app.models.tattoo import TattooBooking, TattooReview, BookingStatus, ReviewStatus
from app.models.whitelist import PlatformWhitelist

logger = logging.getLogger(__name__)

NICHE_EMOJI = {
    BotNiche.LABOR:  "💼",
    BotNiche.BEAUTY: "💅",
    BotNiche.SPORTS: "🏋️",
}


# ── FSM ───────────────────────────────────────────────────────────────────────

class PlatformBroadcastFSM(StatesGroup):
    message = State()
    confirm = State()


def _is_owner(user_id: int) -> bool:
    return user_id == settings.PLATFORM_OWNER_ID


# ── /start → admin panel ──────────────────────────────────────────────────────

async def owner_start(message: types.Message) -> None:
    await _show_panel(message)


# ── Bot list (paginated, 10 per page) ─────────────────────────────────────────

async def pa_bots(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
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

    total_res = await session.execute(select(func.count(RegisteredBot.id)))
    total = total_res.scalar_one()

    if not bots:
        await callback.message.edit_text("😔 Ботів ще немає.")
        await callback.answer()
        return

    lines = []
    for b in bots:
        status = "🟢" if b.is_active else "🔴"
        emoji = NICHE_EMOJI.get(b.niche, "🤖")
        lines.append(
            f"{status} {emoji} @{b.bot_username}\n"
            f"    👤 owner: <code>{b.owner_telegram_id}</code> | {b.created_at.strftime('%d.%m.%Y')}"
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


# ── Single bot detail ─────────────────────────────────────────────────────────

async def pa_bot_detail(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    # Count bookings and reviews if BEAUTY
    extra = ""
    if bot.niche == BotNiche.BEAUTY:
        bookings_res = await session.execute(
            select(func.count(TattooBooking.id)).where(TattooBooking.bot_id == bot_id)
        )
        reviews_res = await session.execute(
            select(func.count(TattooReview.id)).where(
                TattooReview.bot_id == bot_id,
                TattooReview.status == ReviewStatus.APPROVED,
            )
        )
        active_bookings_res = await session.execute(
            select(func.count(TattooBooking.id)).where(
                TattooBooking.bot_id == bot_id,
                TattooBooking.status == BookingStatus.NEW,
            )
        )
        total_b = bookings_res.scalar_one()
        approved_r = reviews_res.scalar_one()
        active_b = active_bookings_res.scalar_one()
        extra = (
            f"\n\n📅 Записів всього: <b>{total_b}</b> (активних: {active_b})\n"
            f"⭐️ Відгуків схвалено: <b>{approved_r}</b>"
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

    await callback.message.edit_text(
        f"🤖 <b>@{bot.bot_username}</b>\n\n"
        f"🆔 Bot ID (для .env): <code>{bot.id}</code>\n"
        f"📦 Ніша: {NICHE_EMOJI.get(bot.niche, '')} {bot.niche.value}\n"
        f"👤 Owner ID: <code>{bot.owner_telegram_id}</code>\n"
        f"📅 Зареєстровано: {bot.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"💳 Підписка: {sub_text}\n"
        f"Статус: {status_text}"
        f"{extra}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=toggle_text,      callback_data=f"pa:toggle:{bot_id}")],
            [types.InlineKeyboardButton(text="📅 +30 днів",    callback_data=f"pa:sub_extend:{bot_id}")],
            [types.InlineKeyboardButton(text="◀️ Список ботів", callback_data="pa:bots:0")],
        ]),
    )
    await callback.answer()


# ── Toggle bot active/inactive ────────────────────────────────────────────────

async def pa_toggle(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
    bot_id = int(callback.data.split(":")[2])
    bot = await session.get(RegisteredBot, bot_id)
    if not bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    bot.is_active = not bot.is_active
    await session.commit()
    state = "увімкнено 🟢" if bot.is_active else "вимкнено 🔴"
    await callback.answer(f"@{bot.bot_username} {state}", show_alert=True)

    # Refresh detail view
    callback.data = f"pa:bot:{bot_id}"
    await pa_bot_detail(callback, session)


# ── Extend subscription ───────────────────────────────────────────────────────

async def pa_sub_extend(
    callback: types.CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    bot_id = int(callback.data.split(":")[2])
    reg_bot = await session.get(RegisteredBot, bot_id)
    if not reg_bot:
        await callback.answer("Бот не знайдено", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    base = max(reg_bot.subscription_expires_at or now, now)
    reg_bot.subscription_expires_at = base + timedelta(days=30)
    reg_bot.is_active = True
    await session.commit()

    new_date = reg_bot.subscription_expires_at.strftime("%d.%m.%Y")
    await callback.answer(f"✅ Підписку продовжено до {new_date}", show_alert=True)

    # Notify bot owner
    try:
        await bot.send_message(
            chat_id=reg_bot.owner_telegram_id,
            text=(
                f"✅ <b>Підписку на @{reg_bot.bot_username} продовжено!</b>\n\n"
                f"📅 Активна до: <b>{new_date}</b>"
            ),
        )
    except Exception:
        pass

    callback.data = f"pa:bot:{bot_id}"
    await pa_bot_detail(callback, session)


# ── Platform-wide stats ───────────────────────────────────────────────────────

async def pa_stats(
    callback: types.CallbackQuery,
    session: AsyncSession,
) -> None:
    total_bots = (await session.execute(select(func.count(RegisteredBot.id)))).scalar_one()
    active_bots = (await session.execute(
        select(func.count(RegisteredBot.id)).where(RegisteredBot.is_active == True)
    )).scalar_one()

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
        f"  {NICHE_EMOJI.get(row.niche, '🤖')} {row.niche.value}: {row.cnt}"
        for row in by_niche.all()
    ) or "  —"

    await callback.message.edit_text(
        f"📊 <b>MasterLug — Загальна аналітика</b>\n\n"
        f"🤖 Ботів всього: <b>{total_bots}</b> (активних: {active_bots})\n\n"
        f"По нішах:\n{niche_lines}\n\n"
        f"📅 Записів (Beauty): <b>{total_bookings}</b> (активних: {active_bookings})\n"
        f"⭐️ Відгуків схвалено: <b>{total_reviews}</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home")],
        ]),
    )
    await callback.answer()


# ── Whitelist approve / decline ───────────────────────────────────────────────

async def wl_approve(
    callback: types.CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    parts = callback.data.split(":")  # wl:approve:telegram_id:name
    telegram_id = int(parts[2])
    name = parts[3] if len(parts) > 3 else "Клієнт"

    existing = await session.execute(
        select(PlatformWhitelist).where(PlatformWhitelist.telegram_id == telegram_id)
    )
    if not existing.scalar_one_or_none():
        session.add(PlatformWhitelist(telegram_id=telegram_id, full_name=name))
        await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"✅ {name} додано до whitelist", show_alert=True)

    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=(
                "✅ <b>Вашу заявку схвалено!</b>\n\n"
                "Тепер ви можете зареєструвати свого бота.\n"
                "Натисніть /start щоб продовжити."
            ),
        )
    except Exception:
        logger.warning("Could not notify approved user %s", telegram_id)


async def wl_decline(callback: types.CallbackQuery, bot: Bot) -> None:
    telegram_id = int(callback.data.split(":")[2])
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("❌ Заявку відхилено", show_alert=True)
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text="❌ На жаль, вашу заявку відхилено. Зв'яжіться з адміністратором для деталей.",
        )
    except Exception:
        pass


async def pa_whitelist(callback: types.CallbackQuery, session: AsyncSession) -> None:
    result = await session.execute(
        select(PlatformWhitelist).order_by(PlatformWhitelist.added_at.desc())
    )
    users = list(result.scalars().all())
    if not users:
        await callback.message.edit_text(
            "👥 Whitelist порожній.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
                types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home")
            ]]),
        )
        await callback.answer()
        return

    lines = []
    for u in users:
        mention = f"@{u.username}" if u.username else u.full_name or "—"
        lines.append(f"• {mention} <code>{u.telegram_id}</code>")

    rows = [[types.InlineKeyboardButton(
        text=f"🗑 {u.full_name or u.telegram_id}",
        callback_data=f"wl:remove:{u.telegram_id}",
    )] for u in users]
    rows.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="pa:home")])

    await callback.message.edit_text(
        f"👥 <b>Whitelist ({len(users)})</b>\n\n" + "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def wl_remove(callback: types.CallbackQuery, session: AsyncSession) -> None:
    telegram_id = int(callback.data.split(":")[2])
    result = await session.execute(
        select(PlatformWhitelist).where(PlatformWhitelist.telegram_id == telegram_id)
    )
    entry = result.scalar_one_or_none()
    if entry:
        await session.delete(entry)
        await session.commit()
        await callback.answer("🗑 Видалено з whitelist", show_alert=True)
    await pa_whitelist(callback, session)


# ── Home ──────────────────────────────────────────────────────────────────────

async def pa_home(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>MasterLug — Панель власника платформи</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🤖 Всі боти",           callback_data="pa:bots:0")],
            [types.InlineKeyboardButton(text="📊 Загальна аналітика", callback_data="pa:stats")],
            [types.InlineKeyboardButton(text="👥 Whitelist",          callback_data="pa:whitelist")],
            [types.InlineKeyboardButton(text="📣 Розсилка",           callback_data="pa:broadcast")],
        ]),
    )
    await callback.answer()


async def _show_panel(message: types.Message) -> None:
    await message.answer(
        "🛠 <b>MasterLug — Панель власника платформи</b>",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🤖 Всі боти",           callback_data="pa:bots:0")],
            [types.InlineKeyboardButton(text="📊 Загальна аналітика", callback_data="pa:stats")],
            [types.InlineKeyboardButton(text="👥 Whitelist",          callback_data="pa:whitelist")],
            [types.InlineKeyboardButton(text="📣 Розсилка",           callback_data="pa:broadcast")],
        ]),
    )


# ── Platform Broadcast FSM ────────────────────────────────────────────────────

async def pa_broadcast_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "📣 <b>Платформна розсилка</b>\n\n"
        "Надішліть текст для розсилки всім власникам ботів.\n"
        "(підтримується фото з підписом)",
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
        f"📋 <b>Попередній перегляд розсилки:</b>\n\n{preview}",
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

    # Get all distinct owner_telegram_ids, excluding demo bots
    demo_ids = []
    if settings.DEMO_BOT_LABOR_ID:
        demo_ids.append(settings.DEMO_BOT_LABOR_ID)
    if settings.DEMO_BOT_BEAUTY_ID:
        demo_ids.append(settings.DEMO_BOT_BEAUTY_ID)

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
        f"✅ <b>Платформну розсилку завершено!</b>\n\n"
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
    dp.message.register(owner_start, Command("admin"), F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_home,       F.data == "pa:home",               F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_bots,       F.data.startswith("pa:bots:"),      F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_bot_detail, F.data.startswith("pa:bot:"),       F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_toggle,      F.data.startswith("pa:toggle:"),     F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_sub_extend, F.data.startswith("pa:sub_extend:"), F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_stats,      F.data == "pa:stats",               F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(pa_whitelist,  F.data == "pa:whitelist",           F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(wl_remove,     F.data.startswith("wl:remove:"),    F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(wl_approve,    F.data.startswith("wl:approve:"),   F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(wl_decline,    F.data.startswith("wl:decline:"),   F.from_user.id == settings.PLATFORM_OWNER_ID)
    dp.callback_query.register(lambda c: c.answer(), F.data == "pa:noop")

    # Platform Broadcast FSM
    dp.callback_query.register(
        pa_broadcast_start, F.data == "pa:broadcast",
        F.from_user.id == settings.PLATFORM_OWNER_ID,
    )
    dp.message.register(
        pa_broadcast_got,
        PlatformBroadcastFSM.message,
        F.from_user.id == settings.PLATFORM_OWNER_ID,
    )
    dp.callback_query.register(
        pa_broadcast_confirm, F.data == "pa:broadcast_confirm",
        PlatformBroadcastFSM.confirm,
        F.from_user.id == settings.PLATFORM_OWNER_ID,
    )
    dp.callback_query.register(
        pa_broadcast_cancel, F.data == "pa:broadcast_cancel",
        F.from_user.id == settings.PLATFORM_OWNER_ID,
    )
