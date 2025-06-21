# app/handlers/subscription.py
# Обработчики для меню подписки и получения наград.

import logging
from datetime import datetime, timezone

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.markdown import hcode
from aiogram.exceptions import TelegramBadRequest

from app.database import Database
from app.config import ADMIN_IDS, REWARD_CHANNELS, REWARD_LIMIT, LIMITS, PRICES, MODELS
from app.keyboards.callbacks import Menu, SubscriptionDetails, Reward
from app.keyboards.inline import (
    get_subscription_menu, get_subscription_details_menu, get_reward_menu, get_main_menu
)
from app.services.user_service import (
    get_user_level, get_user_limits, check_authentication, get_user_details_cached, invalidate_user_cache
)

logger = logging.getLogger(__name__)
router = Router()

async def show_reward_offer(message: Message):
    if not REWARD_CHANNELS:
        await message.answer('Достигнут дневной лимит запросов.')
        return

    text = (
        "<b>Закончились бесплатные запросы!</b>\n\n"
        f"Хотите получить больше? Подпишитесь на наши каналы и получите "
        f"<b>{REWARD_LIMIT} запросов в день</b> вместо {LIMITS[0]['daily']}!\n"
    )
    await message.answer(text, reply_markup=get_reward_menu(REWARD_CHANNELS), disable_web_page_preview=True)

@router.callback_query(Menu.filter(F.action == 'subscription'))
async def subscription_menu_handler(callback: CallbackQuery, state: FSMContext, db: Database, cache: dict, bot: Bot):
    if not await check_authentication(callback.from_user, db, state, bot):
        await callback.answer("Сначала пройдите проверку.", show_alert=True)
        return
    
    await callback.answer()
    user_id = callback.from_user.id
    user_level = await get_user_level(user_id, db)

    if user_id in ADMIN_IDS:
        text = (
            "<b>Ваш статус: Администратор</b>\n\n"
            "Текущий план: Max (∞)\n"
            "Вам доступен весь функционал."
        )
    else:
        requests_today = await db.get_user_requests_today(user_id)
        max_requests_today = await db.get_user_requests_today(user_id, is_max_mode=True)
        daily_limit, max_mode_limit = await get_user_limits(user_id, db)
        details = await get_user_details_cached(user_id, db, cache)
        has_bonus = details[8] if details else False
        plan_name = {0: "Free", 1: "Standard", 2: "Premium", 3: "Max"}[user_level]
        if user_level == 0 and has_bonus:
            plan_name = "Free (Бонусный)"

        text = f'<b>⭐ Ваш план: {plan_name}</b>\n\n'
        text += f'Использовано сегодня:\n'
        text += f' • Обычные запросы: {requests_today} / {daily_limit}\n'
        if user_level == 3:
            text += f' • Max Mode запросы: {max_requests_today} / {max_mode_limit}\n'

        sub_end_str = details[3] if details else None
        if sub_end_str and user_level > 0:
            try:
                subscription_end = datetime.fromisoformat(sub_end_str)
                if subscription_end > datetime.now(timezone.utc):
                    remaining = subscription_end - datetime.now(timezone.utc)
                    text += f'\nДо конца подписки: {remaining.days} д {remaining.seconds // 3600} ч\n'
            except (ValueError, TypeError):
                pass
    try:
        await callback.message.edit_text(text, reply_markup=get_subscription_menu())
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in subscription_menu_handler: {e}")

@router.callback_query(SubscriptionDetails.filter())
async def subscription_details_handler(callback: CallbackQuery, callback_data: SubscriptionDetails):
    await callback.answer()
    level = callback_data.level
    plan_name = {1: "Standard", 2: "Premium", 3: "Max"}[level]
    price = PRICES[level]
    limits = LIMITS[level]

    accessible_models_levels = ['free']
    if level >= 1: accessible_models_levels.append('standard')
    if level >= 2: accessible_models_levels.append('premium')

    models_set = set(m for lvl in accessible_models_levels for m in MODELS.get(lvl, []))
    models_text_html = ", ".join(sorted(list(models_set)))

    text_html = (
        f"<b>Подписка «{plan_name}»</b>\n\n"
        f"<b>Цена:</b> {price}₽ / месяц\n"
        f"<b>Лимиты:</b>\n"
        f" • {limits['daily']} обычных запросов в день\n"
    )
    if limits['max_mode'] > 0:
        text_html += f" • {limits['max_mode']} Max Mode запросов в день\n"
    text_html += f"\n<b>Доступ к моделям:</b>\n<pre>{models_text_html}</pre>"

    try:
        await callback.message.edit_text(text_html, reply_markup=get_subscription_details_menu(level))
    except TelegramBadRequest as e:
        if "message is not modified" not in e.message:
            logger.error(f"Error in subscription_details_handler: {e}")

@router.callback_query(Reward.filter(F.action == "check"))
async def check_reward_subscription_handler(callback: CallbackQuery, db: Database, cache: dict):
    user_id = callback.from_user.id
    if not REWARD_CHANNELS:
        return await callback.answer("Бонусная программа временно неактивна.", show_alert=True)

    try:
        for channel in REWARD_CHANNELS:
            member = await callback.bot.get_chat_member(chat_id=channel['id'], user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                await callback.answer(f"Вы не подписаны на канал {channel['name']}. Пожалуйста, проверьте подписки.", show_alert=True)
                return

        await db.set_reward_bonus(user_id)
        invalidate_user_cache(user_id, cache)
        await callback.answer("Бонус получен!", show_alert=True)
        await callback.message.edit_text(
            f"🎉 Отлично! Ваш дневной лимит увеличен до <b>{REWARD_LIMIT}</b> запросов. Можете продолжать.",
            reply_markup=await get_main_menu(user_id, db)
        )
    except Exception as e:
        logger.error(f"Error checking reward subscription for {user_id}: {e}")
        await callback.answer("Не удалось выполнить проверку. Возможно, вы не подписаны на все каналы или возникла ошибка. Попробуйте позже.", show_alert=True)