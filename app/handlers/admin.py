# app/handlers/admin.py

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router, Bot
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.markdown import hcode
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from app.database import Database
from app.config import ADMIN_IDS, MSK_TZ
from app.states import Admin as AdminState
# --- ИЗМЕНЕНИЕ: импортируем новые классы ---
from app.keyboards.callbacks import Menu, AdminMenu, AdminUserAction, AdminUserBrowse
from app.keyboards.inline import (
    get_admin_menu, get_admin_users_menu, get_user_card_menu, 
    get_user_browse_menu, get_back_to_admin_menu
)
from app.services.user_service import (
    get_user_id_from_input, invalidate_user_cache, get_user_limits
)

logger = logging.getLogger(__name__)
router = Router()

# --- Фильтр для проверки прав администратора ---
class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user.id in ADMIN_IDS

# Применяем фильтр ко всему роутеру - это нормально, когда колбэки не пересекаются
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

# --- Вспомогательные функции ---
async def format_user_card(user_id: int, db: Database) -> tuple[str, AdminUserAction | None]:
    details = await db.get_user_details(user_id)
    if not details:
        return f"Пользователь с ID {user_id} не найден в базе.", None
        
    (uid, uname, s_level, s_end, blocked, last_model, created, verified, 
     rewarded, last_image_model, user_instr, user_temp) = details
     
    plan_name = {0: "Free", 1: "Standard", 2: "Premium", 3: "Max"}[s_level]
    if s_level == 0 and rewarded:
        plan_name = "Free (Бонусный)"
        
    s_end_str = "N/A"
    if s_end and s_level > 0:
        try:
            s_end_dt = datetime.fromisoformat(s_end).astimezone(MSK_TZ)
            s_end_str = s_end_dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError): pass
    
    created_dt = datetime.fromisoformat(created).astimezone(MSK_TZ)
    created_str = created_dt.strftime('%Y-%m-%d %H:%M')
    
    requests_today = await db.get_user_requests_today(uid)
    max_requests_today = await db.get_user_requests_today(uid, is_max_mode=True)
    daily_limit, max_limit = await get_user_limits(uid, db)
    
    text = [
        f"<b>Карточка пользователя</b>",
        f"<b>ID:</b> {hcode(str(uid))}",
        f"<b>Username:</b> @{uname or 'N/A'}",
        f"<b>Статус:</b> {'❌ Заблокирован' if blocked else '✅ Активен'}",
        f"<b>Верификация:</b> {'✅ Пройдена' if verified else '❌ Не пройдена'}",
        f"<b>План:</b> {plan_name} (до {s_end_str})" if s_level > 0 else f"<b>План:</b> {plan_name}",
        f"<b>Запросы сегодня:</b> {requests_today}/{daily_limit if daily_limit != float('inf') else '∞'}",
    ]
    if s_level == 3:
        text.append(f"<b>Max запросы сегодня:</b> {max_requests_today}/{max_limit if max_limit != float('inf') else '∞'}")
    
    text.extend([
        f"<b>Последняя модель:</b> {hcode(last_model or 'N/A')}",
        f"<b>Дата регистрации:</b> {created_str} МСК"
    ])
    
    keyboard = get_user_card_menu(user_id=uid, is_blocked=bool(blocked))
    return "\n".join(text), keyboard

# --- Основные меню админ-панели ---
@router.callback_query(Menu.filter(F.action == 'admin'))
async def admin_main_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text('👑 Админ-панель', reply_markup=get_admin_menu())

@router.callback_query(AdminMenu.filter(F.level == 1 and F.action == 'back'))
async def back_to_admin_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text('👑 Админ-панель', reply_markup=get_admin_menu())

# --- Управление пользователями ---
@router.callback_query(AdminMenu.filter(F.level == 0 and F.action == 'users'))
async def admin_users_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text('👥 Управление пользователями', reply_markup=get_admin_users_menu())

# --- Поиск, выдача подписки и т.д. ---
@router.callback_query(AdminMenu.filter(F.level == 1 and F.action == 'find_user'))
async def find_user_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminState.waiting_for_find_user)
    await callback.message.edit_text("Отправьте ID или @username пользователя для поиска.")

@router.message(AdminState.waiting_for_find_user)
async def find_user_process(message: Message, state: FSMContext, db: Database):
    await state.clear()
    user_id = await get_user_id_from_input(message.text.strip(), db)
    if not user_id:
        await message.answer(f"Пользователь {message.text} не найден.", reply_markup=get_back_to_admin_menu())
        return
    card_text, card_keyboard = await format_user_card(user_id, db)
    await message.answer(card_text, reply_markup=card_keyboard)

# --- Действия из карточки пользователя ---
@router.callback_query(AdminUserAction.filter())
async def handle_user_action(callback: CallbackQuery, callback_data: AdminUserAction, db: Database, cache: dict, bot: Bot):
    user_id = callback_data.user_id
    action = callback_data.action

    actions = {
        'block': (lambda uid: db.block_user(uid, True), f"Пользователь {user_id} заблокирован."),
        'unblock': (lambda uid: db.block_user(uid, False), f"Пользователь {user_id} разблокирован."),
        'revoke': (lambda uid: db.update_subscription(uid, 0), f"Подписка для {user_id} отозвана.")
    }
    
    if action in actions:
        action_func, success_msg = actions[action]
        await action_func(user_id)
        invalidate_user_cache(user_id, cache)
        await callback.answer(success_msg, show_alert=True)
        
        # Обновляем карточку
        try:
            card_text, card_keyboard = await format_user_card(user_id, db)
            await callback.message.edit_text(card_text, reply_markup=card_keyboard)
        except TelegramBadRequest as e:
            if "message is not modified" not in e.message:
                logger.error(f"Failed to update user card for {user_id} after action: {e}")

        # Уведомляем пользователя
        notification_text = {
            'block': 'Ваш доступ к моделям был заблокирован администратором.',
            'unblock': 'Ваш доступ к моделям был разблокирован администратором.',
            'revoke': 'Ваша подписка была отозвана администратором. Установлен уровень Free.'
        }.get(action)
        if notification_text:
            try:
                await bot.send_message(user_id, notification_text)
            except TelegramForbiddenError:
                logger.warning(f"Could not notify user {user_id}, bot is blocked.")
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")

# --- Постраничный просмотр ---
@router.callback_query(AdminUserBrowse.filter())
async def browse_users_handler(callback: CallbackQuery, callback_data: AdminUserBrowse, db: Database):
    await callback.answer()
    page = callback_data.page
    total_users = await db.get_user_count()
    total_pages = (total_users + 1 - 1) // 1 if total_users > 0 else 1
    
    if not 1 <= page <= total_pages: return
    user_data = await db.get_users_paginated(page=page, page_size=1)
    if not user_data:
        await callback.message.edit_text("Пользователи не найдены.", reply_markup=get_back_to_admin_menu())
        return
        
    user_id = user_data[0][0]
    card_text, _ = await format_user_card(user_id, db)
    card_text += f"\n\n<i>Пользователь {page} из {total_users}</i>"
    
    try:
        await callback.message.edit_text(card_text, reply_markup=get_user_browse_menu(page, total_pages))
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in browse_users_handler: {e}")

# --- Выдача подписки ---
@router.callback_query(AdminMenu.filter(F.level == 1 and F.action == 'grant'))
async def grant_sub_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AdminState.waiting_for_grant)
    await callback.message.edit_text(
        'Отправьте ID или @username пользователя, уровень подписки (1, 2, или 3) и кол-во дней (опционально, по умолч. 30).\n'
        'Формат: <code>ID/username LEVEL [DAYS]</code>',
    )

@router.message(AdminState.waiting_for_grant)
async def grant_sub_process(message: Message, state: FSMContext, db: Database, cache: dict):
    await state.clear()
    parts = message.text.split()
    try:
        target_input = parts[0]
        level = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
        if level not in [1, 2, 3]: raise ValueError("Invalid subscription level.")
        user_id = await get_user_id_from_input(target_input, db)
        if user_id:
            await db.update_subscription(user_id, level, days=days)
            invalidate_user_cache(user_id, cache)
            await message.answer(f'Подписка уровня {level} на {days} дней выдана пользователю {target_input}.', reply_markup=get_back_to_admin_menu())
            logger.info(f"Admin {message.from_user.id} granted level {level} for {days} days to user {user_id}")
        else:
            await message.answer(f'Пользователь {target_input} не найден.', reply_markup=get_back_to_admin_menu())
    except (ValueError, IndexError):
        await message.answer('Неверный формат. Пожалуйста, проверьте данные.', reply_markup=get_back_to_admin_menu())

# --- Статистика, Рассылка, Отчеты ---
@router.callback_query(AdminMenu.filter(F.level == 0))
async def admin_main_actions(callback: CallbackQuery, callback_data: AdminMenu, db: Database, cache: dict, state: FSMContext):
    action = callback_data.action
    if action == 'stats':
        await callback.answer()
        total_users = await db.get_user_count()
        stats = await db.get_subscription_stats()
        text = (f'<b>📊 Статистика:</b>\n\nВсего пользователей: {total_users}\n'
                f' • Free: {stats.get(0, 0)}\n • Standard: {stats.get(1, 0)}\n'
                f' • Premium: {stats.get(2, 0)}\n • Max: {stats.get(3, 0)}')
        await callback.message.edit_text(text, reply_markup=get_back_to_admin_menu())
    elif action == 'report':
        await callback.answer()
        report_text = cache.get("model_status", {}).get("last_report", "Отчет еще не был сгенерирован.")
        await callback.message.edit_text(report_text, reply_markup=get_back_to_admin_menu())
    elif action == 'broadcast':
        await callback.answer()
        await state.set_state(AdminState.waiting_for_broadcast)
        await callback.message.edit_text("Введите текст для рассылки. Он будет отправлен всем пользователям.")

@router.message(AdminState.waiting_for_broadcast)
async def broadcast_process(message: Message, state: FSMContext, db: Database, bot: Bot):
    await state.clear()
    await message.answer("Начинаю рассылку...")
    user_ids = await db.get_all_user_ids()
    success_count, fail_count = 0, 0
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, message.text)
            success_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.1)
    completion_text = f"✅ Рассылка завершена.\n\nУспешно: {success_count}\nНеудачно: {fail_count}"
    await message.answer(completion_text, reply_markup=get_back_to_admin_menu())