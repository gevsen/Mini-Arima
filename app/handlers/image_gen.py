# app/handlers/image_gen.py
# Обработчики для генерации изображений.

import logging
import asyncio
import aiohttp
import time

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.markdown import hcode
from aiogram.exceptions import TelegramBadRequest

from app.database import Database
from app.config import IMAGE_MODELS, API_URL, API_KEY
from app.states import ImageGen as ImageGenState
from app.keyboards.callbacks import Menu, SelectImageModel
from app.keyboards.inline import get_image_models_menu, get_main_menu
from app.services.user_service import get_user_level, get_user_limits, check_authentication, invalidate_user_cache
from app.services.system_service import is_model_available, set_model_failed_in_cache
from .chat import animate_waiting, send_limit_reached_message

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(Menu.filter(F.action == 'image_gen'))
async def start_image_gen_handler(callback: CallbackQuery, state: FSMContext, db: Database, cache: dict, bot: Bot):
    if not await check_authentication(callback.from_user, db, state, bot):
        await callback.answer("Сначала пройдите проверку.", show_alert=True)
        return

    user_level = await get_user_level(callback.from_user.id, db)
    if user_level < 2:
        await callback.answer("🎨 Генерация изображений доступна только для подписчиков Premium и Max.", show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(ImageGenState.waiting_for_model)
    try:
        await callback.message.edit_text(
            "Выберите модель для генерации изображения:",
            reply_markup=get_image_models_menu(IMAGE_MODELS, cache['model_status'].get('statuses', {}))
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in start_image_gen_handler: {e}")

@router.callback_query(SelectImageModel.filter(F.status == "failed"))
async def select_failed_image_model(callback: CallbackQuery):
    await callback.answer("⚠️ Эта модель сейчас недоступна. Выберите другую.", show_alert=True)

@router.callback_query(SelectImageModel.filter(F.status == "ok"), ImageGenState.waiting_for_model)
async def select_image_model_handler(callback: CallbackQuery, callback_data: SelectImageModel, state: FSMContext, db: Database, cache: dict):
    await callback.answer()
    await db.set_last_used_image_model(callback.from_user.id, callback_data.model_name)
    invalidate_user_cache(callback.from_user.id, cache)

    await state.update_data(image_model=callback_data.model_name)
    await state.set_state(ImageGenState.waiting_for_prompt)

    await callback.message.edit_text(f"Выбрана модель: <b>{callback_data.model_name}</b>.\n\nТеперь отправьте мне текстовый промпт.")

@router.message(ImageGenState.waiting_for_prompt)
async def generate_image_handler(message: Message, state: FSMContext, db: Database, cache: dict):
    user_id = message.from_user.id
    user_data = await state.get_data()
    model = user_data.get('image_model')

    if not model:
        await message.answer("Произошла ошибка, модель не была выбрана. Пожалуйста, начните заново.", parse_mode=None)
        await state.clear()
        return

    if not is_model_available(model, cache):
        await message.answer(
            f"😥 Модель <b>{model}</b> сейчас недоступна.\n\n"
            "Пожалуйста, выберите другую модель для генерации.",
            reply_markup=await get_main_menu(user_id, db)
        )
        await state.clear()
        return

    daily_limit, _ = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=False)

    if requests_today >= daily_limit:
        await state.clear()
        await send_limit_reached_message(message, db)
        return

    prompt = message.text
    await state.clear()

    # --- ИЗМЕНЕНИЕ: Отправляем сообщение сразу с первым кадром анимации ---
    msg = await message.answer("Творю... ⏳")
    animation_task = asyncio.create_task(animate_waiting(msg, text="Творю"))
    
    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        url = f"{API_URL}/images/generations"
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "prompt": prompt, "height": 1024, "width": 1024, "response_format": "url"}
        try:
            async with session.post(url, headers=headers, json=payload, timeout=180) as response:
                animation_task.cancel()
                if response.status == 200:
                    duration = time.time() - start_time
                    data = await response.json()
                    image_url = data['data'][0]['url']
                    await db.add_request(user_id, model, is_max_mode=False)
                    await msg.delete()
                    await message.answer_photo(
                        photo=image_url, 
                        caption=f"✅ Готово!\n\n<b>Модель:</b> {hcode(model)}\n<b>Время:</b> {duration:.2f} сек.\n<b>Промпт:</b> {hcode(prompt)}"
                    )
                else:
                    set_model_failed_in_cache(model, cache)
                    error_text = await response.text()
                    await msg.edit_text(f"😥 Произошла ошибка при генерации.\n<b>Статус:</b> {response.status}\n<b>Ответ:</b> {error_text}")
        except Exception as e:
            animation_task.cancel()
            set_model_failed_in_cache(model, cache)
            logger.error(f"Image generation failed for user {user_id} with model {model}. Error: {e}", exc_info=True)
            await msg.edit_text(f"😥 Критическая ошибка: {e}", parse_mode=None)