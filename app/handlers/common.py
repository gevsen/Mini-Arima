# app/handlers/common.py
# Обработчики основных команд, таких как start, menu, help и капча.

import logging
import random
from datetime import datetime

from aiogram import F, Router, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.markdown import hcode
from aiogram.exceptions import TelegramBadRequest

from app.database import Database
from app.config import ADMIN_IDS, CAPTCHA_VARIANTS, MSK_TZ, LIMITS, REWARD_LIMIT
from app.states import Captcha, Chat, MaxMode
from app.keyboards.inline import get_main_menu, get_chat_menu
from app.keyboards.callbacks import Menu, Chat as ChatCallback
from app.services.user_service import invalidate_user_cache, check_authentication, get_user_level

logger = logging.getLogger(__name__)
router = Router()

# --- Обработчики команд ---
@router.message(Command('start'), F.chat.type == "private")
async def start_handler(message: Message, state: FSMContext, db: Database, bot: Bot, cache: dict):
    await state.clear()
    user = message.from_user
    
    # Проверяем, есть ли пользователь в БД, и добавляем, если нет
    user_in_db = await db.get_user(user.id)
    if not user_in_db:
        is_new = await db.add_user(user.id, user.username)
        if is_new:
            logger.info(f"New user registered: {user.full_name} (@{user.username}, ID: {user.id})")
            notification_text = f"🎉 Новый пользователь!\n\nID: {hcode(str(user.id))}\nUsername: @{user.username or 'N/A'}"
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, notification_text)
                except Exception as e:
                    logger.warning(f"Failed to send new user notification to admin {admin_id}: {e}")
            await db.set_user_verified(user.id, False)
            invalidate_user_cache(user.id, cache)

    # Проверяем верификацию (капчу)
    if not await check_authentication(user, db, state, bot):
        return

    # Проверяем, не админ ли это, и выдаем подписку
    if user.id in ADMIN_IDS and await get_user_level(user.id, db) != 3:
        await db.update_subscription(user.id, 3)
        invalidate_user_cache(user.id, cache)
        logger.info(f"Admin user {user.id} detected. Subscription level set to 3 (Max).")

    current_time_msk = datetime.now(MSK_TZ).strftime("%H:%M МСК")
    await message.answer(
        f'Привет, это MiniArima!\n\nТекущее время: <b>{current_time_msk}</b>\n\nВыберите действие:',
        reply_markup=await get_main_menu(user.id, db)
    )

# --- ИЗМЕНЕННЫЙ ХЕНДЛЕР /menu ---
@router.message(Command('menu'), F.chat.type == "private")
async def menu_handler(message: Message, state: FSMContext, db: Database, bot: Bot, cache: dict):
    user = message.from_user
    
    # Шаг 1: Проверяем, есть ли пользователь в БД. Если нет - регистрируем.
    # Это ключевое исправление.
    user_in_db = await db.get_user(user.id)
    if not user_in_db:
        logger.info(f"User {user.id} used /menu without being in DB. Redirecting to start logic.")
        # Просто вызываем хендлер start, чтобы не дублировать код
        await start_handler(message, state, db, bot, cache)
        return

    # Шаг 2: Если пользователь в БД, проверяем его верификацию (капчу).
    if not await check_authentication(user, db, state, bot):
        return

    # Шаг 3: Если все проверки пройдены, показываем соответствующее меню.
    current_state = await state.get_state()
    is_max_mode = current_state == MaxMode.in_progress

    if current_state in [Chat.in_progress, MaxMode.in_progress]:
        await message.answer('Меню диалога:', reply_markup=get_chat_menu(is_max_mode))
    else:
        await state.clear()
        await message.answer(
            'Главное меню:',
            reply_markup=await get_main_menu(message.from_user.id, db)
        )

# --- Обработчики колбэков ---
@router.callback_query(Menu.filter(F.action == 'back_main'))
@router.callback_query(ChatCallback.filter(F.action == 'back_to_main'))
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer()
    await state.clear()
    try:
        await callback.message.edit_text(
            'Главное меню:',
            reply_markup=await get_main_menu(callback.from_user.id, db)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in back_to_main_menu: {e}")

@router.callback_query(Menu.filter(F.action == 'help'))
async def help_handler(callback: CallbackQuery, db: Database):
    await callback.answer()
    text = (
        f'<b>ℹ️ Справка</b>\n\n'
        f'<b>Доступные команды:</b>\n'
        f'<code>/start</code> - главное меню\n'
        f'<code>/menu</code> - меню в любой момент\n\n'
        f'<b>Лимиты запросов в день:</b>\n'
        f' • <b>Free:</b> {LIMITS[0]["daily"]} (или {REWARD_LIMIT} с бонусом)\n'
        f' • <b>Standard:</b> {LIMITS[1]["daily"]}\n'
        f' • <b>Premium:</b> {LIMITS[2]["daily"]}\n'
        f' • <b>Max:</b> {LIMITS[3]["daily"]} обычных + {LIMITS[3]["max_mode"]} Max Mode'
    )
    try:
        await callback.message.edit_text(text, reply_markup=await get_main_menu(callback.from_user.id, db))
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            raise

# --- Обработчики Капчи ---
@router.message(Captcha.waiting_for_answer)
async def process_captcha(message: Message, state: FSMContext, db: Database, cache: dict):
    user_data = await state.get_data()
    correct_answer = user_data.get('captcha_answer')

    if message.text and message.text.strip().lower() == correct_answer.lower():
        await db.set_user_verified(message.from_user.id, True)
        invalidate_user_cache(message.from_user.id, cache)
        await state.clear()
        await message.answer(
            "✅ Верно! Добро пожаловать.",
            reply_markup=await get_main_menu(message.from_user.id, db)
        )
        logger.info(f"User {message.from_user.id} passed captcha.")
    else:
        await message.answer("❌ Неверно. Попробуйте еще раз.")
        question, answer = random.choice(CAPTCHA_VARIANTS)
        await state.update_data(captcha_answer=answer)
        await message.answer(f"Новый вопрос:\n<b>{question}</b>")
        logger.info(f"User {message.from_user.id} failed captcha.")

# --- Обработчик нераспознанных сообщений ---
@router.message(F.chat.type == "private", StateFilter(None))
async def unhandled_private_message(message: Message, state: FSMContext, db: Database, bot: Bot):
    if not await check_authentication(message.from_user, db, state, bot):
        return
    await message.answer(
        'Сначала выберите модель для чата или активируйте Max Mode.',
        reply_markup=await get_main_menu(message.from_user.id, db)
    )