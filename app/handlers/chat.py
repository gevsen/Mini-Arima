# app/handlers/chat.py
# Обработчики для логики чата (обычного и Max Mode).

import logging
import time
import asyncio

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.markdown import hcode
from aiogram.exceptions import TelegramBadRequest
from openai import APIError

from app.database import Database
from app.config import MODEL_CATEGORIES, MODELS, MAX_MODE_PARTICIPANTS, DEFAULT_TEMPERATURE, MAX_MODE_ARBITER
from app.states import Chat, MaxMode
from app.keyboards.callbacks import Menu, Chat as ChatCallback, ModelCategory, SelectTextModel, MaxMode as MaxModeCallback
from app.keyboards.inline import (
    get_model_categories_menu, get_models_menu, get_chat_menu, get_max_mode_activation_menu, get_main_menu
)
from app.services.user_service import (
    get_user_level, get_user_limits, check_authentication, invalidate_user_cache, get_user_details_cached
)
from app.services.system_service import (
    is_model_available, are_max_mode_models_available, set_model_failed_in_cache
)
from app.services.ai_service import get_simple_response, get_max_mode_response
from .subscription import show_reward_offer

logger = logging.getLogger(__name__)
router = Router()

# --- Вспомогательные функции ---
async def animate_waiting(message: Message, text: str = "Думаю"):
    frames = ["⏳", "⌛️"]
    i = 0
    # Начинаем со второго кадра, так как первый уже отправлен
    await asyncio.sleep(1.5)
    i = 1
    while True:
        try:
            await message.edit_text(f"{text}... {frames[i % len(frames)]}")
            i += 1
            await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            break
        except Exception:
            break

async def send_limit_reached_message(message: Message, db: Database):
    user_id = message.from_user.id
    details = await get_user_details_cached(user_id, db, message.bot.get('cache'))
    has_bonus = details[8] if details else False
    user_level = await get_user_level(user_id, db)

    if user_level == 0 and not has_bonus:
        await show_reward_offer(message)
    else:
        await message.answer('Достигнут дневной лимит запросов. Попробуйте снова завтра или рассмотрите улучшение подписки.')

# --- Обработчики выбора модели ---
@router.callback_query(Menu.filter(F.action == 'models'))
async def list_model_categories(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot):
    await callback.answer()
    if not await check_authentication(callback.from_user, db, state, bot):
        return

    user_level = await get_user_level(callback.from_user.id, db)
    accessible_models = set(MODELS['free'])
    if user_level >= 1: accessible_models.update(MODELS['standard'])
    if user_level >= 2: accessible_models.update(MODELS['premium'])

    available_categories = [
        cat for cat, models_in_cat in MODEL_CATEGORIES.items()
        if any(m in accessible_models for m in models_in_cat)
    ]

    try:
        await callback.message.edit_text(
            'Выберите категорию:',
            reply_markup=get_model_categories_menu(available_categories)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in list_model_categories: {e}")

@router.callback_query(ModelCategory.filter())
async def list_models_in_category(callback: CallbackQuery, callback_data: ModelCategory, db: Database, cache: dict):
    await callback.answer()
    category = callback_data.name
    user_level = await get_user_level(callback.from_user.id, db)

    accessible_models = set(MODELS['free'])
    if user_level >= 1: accessible_models.update(MODELS['standard'])
    if user_level >= 2: accessible_models.update(MODELS['premium'])

    category_models = [m for m in MODEL_CATEGORIES.get(category, []) if m in accessible_models]

    try:
        await callback.message.edit_text(
            f'Модели в категории "{category}":',
            reply_markup=get_models_menu(category, category_models, cache['model_status'].get('statuses', {}))
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in list_models_in_category: {e}")

@router.callback_query(SelectTextModel.filter(F.status == "failed"))
async def select_failed_model(callback: CallbackQuery):
    await callback.answer("⚠️ Эта модель сейчас недоступна. Выберите другую.", show_alert=True)

@router.callback_query(SelectTextModel.filter(F.status == "ok"))
async def select_model_handler(callback: CallbackQuery, callback_data: SelectTextModel, state: FSMContext, db: Database, cache: dict):
    await callback.answer()
    user_id = callback.from_user.id
    details = await get_user_details_cached(user_id, db, cache)

    if details and details[4]:
        await callback.message.edit_text('Ваш доступ к моделям заблокирован')
        return

    daily_limit, _ = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=False)

    if requests_today >= daily_limit:
        await send_limit_reached_message(callback.message, db)
        return

    model = callback_data.model_name
    await db.set_last_used_model(user_id, model)
    invalidate_user_cache(user_id, cache)

    await state.set_state(Chat.in_progress)
    await state.update_data(model=model, history=[])
    await callback.message.edit_text(f'Выбрана модель: <b>{model}</b>\nОтправьте ваш запрос.\n\nДля вызова меню используйте /menu')

# --- Обработчики обычного чата ---
@router.callback_query(ChatCallback.filter(F.action == 'new'))
async def new_chat_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Начат новый диалог. Контекст очищен.")
    await state.update_data(history=[])
    model = (await state.get_data()).get('model', 'Не выбрана')
    await callback.message.edit_text(f'<b>Модель: {model}</b>\nОтправьте ваш запрос.')

@router.message(Chat.in_progress)
async def handle_chat_message(message: Message, state: FSMContext, db: Database, ai_client, cache: dict):
    user_id = message.from_user.id
    details = await get_user_details_cached(user_id, db, cache)

    if details and details[4]:
        await message.answer('Ваш доступ к моделям заблокирован администратором.')
        return

    daily_limit, _ = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=False)

    if requests_today >= daily_limit:
        await state.clear()
        await send_limit_reached_message(message, db)
        return

    user_data = await state.get_data()
    model = user_data.get('model')
    history = user_data.get('history', [])

    if not is_model_available(model, cache):
        await message.answer(
            f"😥 Ваша текущая модель <b>{model}</b> сейчас недоступна.\n\n"
            "Пожалуйста, выберите другую модель.",
            reply_markup=get_chat_menu()
        )
        return

    # --- ИЗМЕНЕНИЕ: Отправляем сообщение сразу с первым кадром анимации ---
    msg = await message.answer('Думаю... ⏳')
    animation_task = asyncio.create_task(animate_waiting(msg))
    history.append({"role": "user", "content": message.text})

    try:
        response_text, duration = await get_simple_response(ai_client, model, history, user_id, db, cache)
        animation_task.cancel()
        history.append({"role": "assistant", "content": response_text})
        await state.update_data(history=history[-10:])
        await db.add_request(user_id, model, is_max_mode=False)
        temp = details[11] if details and details[11] is not None else DEFAULT_TEMPERATURE
        footer = f"\n\n---\nМодель: {model} | t: {temp:.1f} | Время: {duration:.2f} сек."
        await msg.edit_text(response_text + footer)
    except (APIError, RuntimeError) as e:
        animation_task.cancel()
        set_model_failed_in_cache(model, cache)
        history.pop()
        await state.update_data(history=history)
        logger.error(f"Chat Error for user {user_id} with model {model}: {e}")
        await msg.edit_text(f"😥 Модель <b>{model}</b> временно недоступна (ошибка сервера).\n\nОна автоматически отключена. Пожалуйста, выберите другую модель.")
    except Exception as e:
        animation_task.cancel()
        history.pop()
        await state.update_data(history=history)
        logger.error(f"Generic Chat Error for user {user_id} with model {model}: {e}", exc_info=True)
        await msg.edit_text(f'Произошла непредвиденная ошибка: {e}')

# --- Обработчики Max Mode ---
@router.callback_query(Menu.filter(F.action == 'max_mode'))
async def max_mode_intro(callback: CallbackQuery, db: Database, cache: dict):
    await callback.answer()
    user_id = callback.from_user.id
    if await get_user_level(user_id, db) != 3:
        await callback.answer("🚀 Max Mode доступен только для подписчиков уровня Max.", show_alert=True)
        return

    if not are_max_mode_models_available(cache):
        await callback.answer("К сожалению, одна или несколько моделей для Max Mode сейчас недоступны. Попробуйте позже.", show_alert=True)
        return

    _, max_mode_limit = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=True)
    models_list_str = "\n".join(f"  • {hcode(m)}" for m in MAX_MODE_PARTICIPANTS)
    text = (
        "<b>🚀 Режим Max Mode</b>\n\n"
        "Это специальный режим, в котором ваш запрос обрабатывается "
        "сразу несколькими ведущими моделями для достижения максимального качества ответа.\n\n"
        f"<b>Модели-участники:</b>\n{models_list_str}\n\n"
        f"<b>Лимит:</b> {requests_today} / {max_mode_limit} запросов в день.\n"
        "Один запрос в этом режиме списывает одну единицу лимита Max Mode."
    )
    await callback.message.edit_text(text, reply_markup=get_max_mode_activation_menu())

@router.callback_query(MaxModeCallback.filter(F.action == "activate"))
async def activate_max_mode(callback: CallbackQuery, state: FSMContext, db: Database, cache: dict):
    await callback.answer()
    user_id = callback.from_user.id
    if not are_max_mode_models_available(cache):
        await callback.answer("К сожалению, одна или несколько моделей для Max Mode сейчас недоступны. Попробуйте позже.", show_alert=True)
        return

    _, max_mode_limit = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=True)

    if requests_today >= max_mode_limit:
        await callback.answer("Достигнут дневной лимит запросов в Max Mode.", show_alert=True)
        return

    await state.set_state(MaxMode.in_progress)
    await callback.message.edit_text(
        "<b>🚀 Max Mode активирован.</b>\n\n"
        "Отправьте ваш запрос. Он будет обработан несколькими моделями одновременно.\n\n"
        "Для выхода из режима используйте /menu."
    )

@router.callback_query(MaxModeCallback.filter(F.action == "exit"))
async def exit_max_mode(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer("Вы вышли из Max Mode.")
    await state.clear()
    await callback.message.edit_text(
        'Главное меню:',
        reply_markup=await get_main_menu(callback.from_user.id, db)
    )

@router.message(MaxMode.in_progress)
async def handle_max_mode_message(message: Message, state: FSMContext, db: Database, ai_client, cache: dict):
    user_id = message.from_user.id
    if not are_max_mode_models_available(cache):
        await message.answer("К сожалению, одна или несколько моделей для Max Mode стали недоступны. Режим автоматически отключен.")
        await state.clear()
        return

    _, max_mode_limit = await get_user_limits(user_id, db)
    requests_today = await db.get_user_requests_today(user_id, is_max_mode=True)

    if requests_today >= max_mode_limit:
        await message.answer("Достигнут дневной лимит запросов в Max Mode. Режим автоматически отключен.")
        await state.clear()
        return

    # --- ИЗМЕНЕНИЕ: То же самое для Max Mode ---
    msg = await message.answer("Обработка несколькими моделями... ⏳")
    animation_task = asyncio.create_task(animate_waiting(msg, text="Обработка несколькими моделями"))

    try:
        response_text, duration = await get_max_mode_response(ai_client, message.text, user_id, db, cache)
        animation_task.cancel()
        await db.add_request(user_id, "max_mode_ensemble", is_max_mode=True)
        participants_str = ", ".join(f"{hcode(m)}" for m in MAX_MODE_PARTICIPANTS)
        footer = (
            f"\n\n"
            f"--- 🚀 Max Mode ---\n"
            f"<b>Участники:</b> {participants_str}\n"
            f"<b>Арбитр:</b> {hcode(MAX_MODE_ARBITER)}\n"
            f"<b>Время:</b> {duration:.2f} сек."
        )
        await msg.edit_text(response_text + footer)
    except RuntimeError as e:
        animation_task.cancel()
        logger.error(f"Max Mode runtime error for user {user_id}: {e}")
        await msg.edit_text(f"😥 <b>Произошла ошибка в Max Mode:</b>\n{e}")
    except Exception as e:
        animation_task.cancel()
        logger.error(f"Generic Max Mode error for user {user_id}: {e}", exc_info=True)
        await msg.edit_text(f"😥 Произошла непредвиденная ошибка в Max Mode: {e}")