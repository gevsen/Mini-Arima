# app/handlers/settings.py
# Обработчики для меню настроек пользователя.

import logging

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.markdown import hcode
from aiogram.exceptions import TelegramBadRequest

from app.database import Database
from app.config import DEFAULT_TEMPERATURE
from app.states import Settings as SettingsState
from app.keyboards.callbacks import Menu, Settings as SettingsCallback
from app.keyboards.inline import get_settings_menu, get_main_menu
from app.services.user_service import check_authentication, get_user_details_cached, invalidate_user_cache

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(Menu.filter(F.action == 'settings'))
async def settings_menu_handler(callback: CallbackQuery, state: FSMContext, db: Database, cache: dict, bot: Bot):
    if not await check_authentication(callback.from_user, db, state, bot):
        await callback.answer("Сначала пройдите проверку.", show_alert=True)
        return
    
    await callback.answer()
    await state.clear()

    user_details = await get_user_details_cached(callback.from_user.id, db, cache)
    instruction = user_details[10] if user_details and user_details[10] else "Не задана"
    temperature = user_details[11] if user_details and user_details[11] is not None else DEFAULT_TEMPERATURE

    text = (
        "<b>⚙️ Настройки</b>\n\n"
        "Здесь вы можете настроить поведение модели под себя.\n\n"
        f"<b>Текущая инструкция:</b>\n{hcode(instruction)}\n\n"
        f"<b>Текущая температура:</b> {hcode(str(temperature))}\n\n"
        "<b>Инструкция</b> - это системное сообщение, которое будет направлять модель в каждом запросе. "
        "<b>Температура</b> (от 0.0 до 2.0) контролирует случайность ответа: низкие значения делают ответ более предсказуемым, высокие - более креативным."
    )
    try:
        await callback.message.edit_text(text, reply_markup=get_settings_menu())
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in settings_menu_handler: {e}")

# --- Инструкция ---
@router.callback_query(SettingsCallback.filter(F.action == "instruction"))
async def settings_instruction_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsState.waiting_for_instruction)
    await callback.message.edit_text("Отправьте текст вашей инструкции (до 1000 символов). Чтобы удалить инструкцию, отправьте `-` (минус).")

@router.message(SettingsState.waiting_for_instruction)
async def settings_instruction_process(message: Message, state: FSMContext, db: Database, cache: dict):
    await state.clear()
    instruction = message.text.strip()

    if len(instruction) > 1000:
        await message.answer("❌ Длина инструкции не должна превышать 1000 символов. Попробуйте снова.")
        await state.set_state(SettingsState.waiting_for_instruction)
        return

    if instruction == "-":
        await db.set_user_instruction(message.from_user.id, None)
        await message.answer("✅ Ваша персональная инструкция удалена.")
    else:
        await db.set_user_instruction(message.from_user.id, instruction)
        await message.answer(f"✅ Ваша персональная инструкция обновлена:\n\n{hcode(instruction)}")

    invalidate_user_cache(message.from_user.id, cache)
    await message.answer("Возвращаю в главное меню...", reply_markup=await get_main_menu(message.from_user.id, db))

# --- Температура ---
@router.callback_query(SettingsCallback.filter(F.action == "temperature"))
async def settings_temperature_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SettingsState.waiting_for_temperature)
    await callback.message.edit_text("Отправьте значение температуры (число от 0.0 до 2.0). Чтобы сбросить к значению по умолчанию, отправьте `-` (минус).")

@router.message(SettingsState.waiting_for_temperature)
async def settings_temperature_process(message: Message, state: FSMContext, db: Database, cache: dict):
    await state.clear()
    temp_str = message.text.strip().replace(',', '.')

    if temp_str == "-":
        await db.set_user_temperature(message.from_user.id, None)
        await message.answer(f"✅ Температура сброшена к значению по умолчанию ({DEFAULT_TEMPERATURE}).")
    else:
        try:
            temperature = float(temp_str)
            if 0.0 <= temperature <= 2.0:
                await db.set_user_temperature(message.from_user.id, temperature)
                await message.answer(f"✅ Температура установлена на {temperature}.")
            else:
                await message.answer("❌ Ошибка. Температура должна быть в диапазоне от 0.0 до 2.0. Попробуйте снова.")
                await state.set_state(SettingsState.waiting_for_temperature)
                return
        except ValueError:
            await message.answer("❌ Ошибка. Пожалуйста, введите число (например, 0.7). Попробуйте снова.")
            await state.set_state(SettingsState.waiting_for_temperature)
            return

    invalidate_user_cache(message.from_user.id, cache)
    await message.answer("Возвращаю в главное меню...", reply_markup=await get_main_menu(message.from_user.id, db))