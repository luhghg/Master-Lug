from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext


def _mode_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="👤 Подивитись як клієнт", callback_data="mode:client")],
        [types.InlineKeyboardButton(text="⚙️ Подивитись як майстер", callback_data="mode:master")],
    ])


async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🖤 <b>TattooBot — демо-версія</b>\n\n"
        "Цей бот показує як виглядає Telegram-бот для тату-майстра <b>Олі</b> з Києва.\n\n"
        "Оберіть режим перегляду:",
        reply_markup=_mode_kb(),
    )


async def cmd_mode(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🔄 Оберіть режим:", reply_markup=_mode_kb())


async def mode_client(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(mode="client")
    await callback.message.edit_text(
        "👤 <b>Режим: Клієнт</b>\n\n"
        "Ви бачите бот очима клієнта тату-майстра Олі.\n"
        "Щоб перемкнути режим — /mode",
    )
    from handlers.client import show_client_menu
    await show_client_menu(callback.message)
    await callback.answer()


async def mode_master(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(mode="master")
    await callback.message.edit_text(
        "⚙️ <b>Режим: Майстер</b>\n\n"
        "Ви бачите панель керування очима майстра.\n"
        "Щоб перемкнути режим — /mode",
    )
    from handlers.master import show_master_menu
    await show_master_menu(callback.message)
    await callback.answer()


def register(dp: Dispatcher) -> None:
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_mode, Command("mode"))
    dp.callback_query.register(mode_client, F.data == "mode:client")
    dp.callback_query.register(mode_master, F.data == "mode:master")
