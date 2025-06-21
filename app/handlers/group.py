# app/handlers/group.py
# Обработчики для сообщений в группах

import logging
import asyncio
import aiohttp
import time

from aiogram import F, Router, Bot
from aiogram.types import Message
from aiogram.utils.markdown import hcode

from app.database import Database
from app.config import (
    GROUP_TEXT_TRIGGER, GROUP_IMAGE_TRIGGER, DEFAULT_TEXT_MODEL, 
    DEFAULT_IMAGE_MODEL, API_URL, API_KEY
)
from app.services.user_service import get_user_details_cached, get_user_limits
from app.services.system_service import is_model_available, set_model_failed_in_cache
from app.services.ai_service import get_simple_response
from .chat import animate_waiting # Импортируем анимацию из соседнего модуля

logger = logging.getLogger(__name__)
router = Router()

# Фильтр, чтобы хендлеры работали только в группах и супергруппах
IS_GROUP = F.chat.type.in_({'group', 'supergroup'})

# --- Обработчик для текстовых запросов (.text) ---
@router.message(IS_GROUP, F.text.startswith(GROUP_TEXT_TRIGGER))
async def handle_group_text_trigger(message: Message, db: Database, ai_client, cache: dict):
    prompt = message.text[len(GROUP_TEXT_TRIGGER):].strip()
    if not prompt:
        return  # Игнорируем, если после триггера ничего нет

    user_id = message.from_user.id
    
    # Проверяем, зарегистрирован ли пользователь и не заблокирован ли он
    user_details = await get_user_details_cached(user_id, db, cache)
    if not user_details or not user_details[7]: # Не зарегистрирован или не прошел капчу
        return
    if user_details[4]: # Заблокирован
        return

    # Проверка лимитов
    daily_limit, _ = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=False)
    if requests_today >= daily_limit:
        try:
            await message.reply("У вас закончились лимиты на сегодня.", disable_notification=True)
        except Exception:
            pass
        return

    model_to_use = user_details[5] or DEFAULT_TEXT_MODEL
    if not is_model_available(model_to_use, cache):
        try:
            await message.reply(f"Модель {hcode(model_to_use)} сейчас недоступна.", disable_notification=True)
        except Exception:
            pass
        return

    # --- ИЗМЕНЕНИЕ: Отправляем сообщение сразу с первым кадром анимации ---
    msg = await message.reply('Думаю над ответом... ⏳', disable_notification=True)
    animation_task = asyncio.create_task(animate_waiting(msg))

    try:
        response_text, duration = await get_simple_response(
            ai_client, model_to_use, [{"role": "user", "content": prompt}], user_id, db, cache
        )
        animation_task.cancel()
        await db.add_request(user_id, model_to_use, is_max_mode=False)
        footer = f"\n\n---\nМодель: {hcode(model_to_use)} | Время: {duration:.2f} сек."
        await msg.edit_text(response_text + footer)
    except Exception as e:
        animation_task.cancel()
        logger.error(f"Group text handler error for user {user_id}: {e}")
        await msg.edit_text("Произошла ошибка при обработке запроса.")


# --- Обработчик для генерации изображений (.image) ---
@router.message(IS_GROUP, F.text.startswith(GROUP_IMAGE_TRIGGER))
async def handle_group_image_trigger(message: Message, db: Database, cache: dict):
    prompt = message.text[len(GROUP_IMAGE_TRIGGER):].strip()
    if not prompt:
        return

    user_id = message.from_user.id

    # Проверяем, зарегистрирован ли пользователь и не заблокирован ли он
    user_details = await get_user_details_cached(user_id, db, cache)
    if not user_details or not user_details[7]: # Не зарегистрирован или не прошел капчу
        return
    if user_details[4]: # Заблокирован
        return
        
    # Проверяем уровень подписки для генерации изображений
    from app.services.user_service import get_user_level
    user_level = await get_user_level(user_id, db)
    if user_level < 2:
        return # Молча игнорируем, если нет нужного уровня

    # Проверка лимитов
    daily_limit, _ = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=False)
    if requests_today >= daily_limit:
        try:
            await message.reply("У вас закончились лимиты на сегодня.", disable_notification=True)
        except Exception:
            pass
        return

    model_to_use = user_details[9] or DEFAULT_IMAGE_MODEL
    if not is_model_available(model_to_use, cache):
        try:
            await message.reply(f"Модель {hcode(model_to_use)} сейчас недоступна.", disable_notification=True)
        except Exception:
            pass
        return

    # --- ИЗМЕНЕНИЕ: Отправляем сообщение сразу с первым кадром анимации ---
    msg = await message.reply('Творю... ⏳', disable_notification=True)
    animation_task = asyncio.create_task(animate_waiting(msg, text="Творю"))
    
    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        url = f"{API_URL}/images/generations"
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        payload = {"model": model_to_use, "prompt": prompt, "height": 1024, "width": 1024, "response_format": "url"}
        try:
            async with session.post(url, headers=headers, json=payload, timeout=180) as response:
                animation_task.cancel()
                if response.status == 200:
                    duration = time.time() - start_time
                    data = await response.json()
                    image_url = data['data'][0]['url']
                    await db.add_request(user_id, model_to_use, is_max_mode=False)
                    await msg.delete()
                    
                    caption_text = (
                        f"<b>Модель:</b> {hcode(model_to_use)}\n"
                        f"<b>Время:</b> {duration:.2f} сек.\n\n"
                        f"<b>Промпт:</b> {hcode(prompt)}"
                    )
                    await message.reply_photo(photo=image_url, caption=caption_text)
                else:
                    set_model_failed_in_cache(model_to_use, cache)
                    error_text = await response.text()
                    await msg.edit_text(f"😥 Произошла ошибка при генерации.\n<b>Статус:</b> {response.status}\n<b>Ответ:</b> {error_text}")
        except Exception as e:
            animation_task.cancel()
            set_model_failed_in_cache(model_to_use, cache)
            logger.error(f"Group image generation failed for user {user_id} with model {model_to_use}. Error: {e}", exc_info=True)
            await msg.edit_text(f"😥 Критическая ошибка: {e}", parse_mode=None)