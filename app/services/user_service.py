# app/services/user_service.py
# Бизнес-логика, связанная с пользователем.

import logging
import random
from datetime import datetime, timezone
from typing import Dict, Tuple

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import User

from app.database import Database
from app.config import ADMIN_IDS, LIMITS, REWARD_LIMIT, CAPTCHA_VARIANTS
from app.states import Captcha

logger = logging.getLogger(__name__)

# --- РЕАЛИЗАЦИЯ РЕКОМЕНДАЦИИ: Функция перенесена из хендлера в сервис ---
async def send_captcha(user_id: int, state: FSMContext, bot: Bot):
    """Отправляет пользователю сообщение с капчей."""
    question, answer = random.choice(CAPTCHA_VARIANTS)
    await state.set_state(Captcha.waiting_for_answer)
    await state.update_data(captcha_answer=answer)
    await bot.send_message(
        user_id,
        f"Чтобы начать, пожалуйста, решите простую задачу:\n<b>{question}</b>\n\nНапишите ответ в чат."
    )
    logger.info(f"Sent captcha to user {user_id}.")


def invalidate_user_cache(user_id: int, cache: Dict):
    """Удаляет данные пользователя из кэша."""
    user_cache = cache.get("user_details")
    if user_cache is not None and user_id in user_cache:
        del user_cache[user_id]
        logger.debug(f"Cache invalidated for user {user_id}")

async def get_user_details_cached(user_id: int, db: Database, cache: Dict):
    """Получает данные пользователя из кэша или БД."""
    user_cache = cache.get("user_details")
    if user_cache is not None and user_id in user_cache:
        logger.debug(f"User details for {user_id} found in cache.")
        return user_cache[user_id]

    logger.debug(f"User details for {user_id} not in cache. Fetching from DB.")
    details = await db.get_user_details(user_id)
    if details and user_cache is not None:
        user_cache[user_id] = details
    return details

async def get_user_level(user_id: int, db: Database) -> int:
    """Определяет уровень подписки пользователя, проверяя срок её действия."""
    if user_id in ADMIN_IDS: return 3 # Администраторы имеют максимальный уровень

    user = await db.get_user(user_id)
    if not user:
        return 0

    level, end_date_str = user[2], user[3]
    if end_date_str and level > 0:
        try:
            end_date = datetime.fromisoformat(end_date_str)
            if end_date < datetime.now(timezone.utc):
                logger.info(f"Subscription expired for user {user_id}. Setting level to 0.")
                await db.update_subscription(user_id, 0)
                # Нет необходимости инвалидировать кэш здесь, т.к. get_user_details_cached его обновит
                return 0
        except (ValueError, TypeError):
             logger.warning(f"Could not parse subscription end_date '{end_date_str}' for user {user_id}")
             pass
    return level

async def get_user_limits(user_id: int, db: Database) -> Tuple[int, int]:
    """Возвращает кортеж (дневной_лимит, лимит_max_mode)."""
    level = await get_user_level(user_id, db)

    # Администраторы
    if user_id in ADMIN_IDS:
        return float('inf'), float('inf')

    # Проверка на бонус за подписку на каналы
    if level == 0:
        details = await db.get_user_details(user_id)
        if details and details[8]: # has_rewarded_bonus
            return REWARD_LIMIT, 0

    plan_limits = LIMITS.get(level, {"daily": 0, "max_mode": 0})
    return plan_limits["daily"], plan_limits["max_mode"]


async def check_authentication(user: User, db: Database, state: FSMContext, bot: Bot) -> bool:
    """
    Проверяет, верифицирован ли пользователь. Если нет, отправляет капчу.
    Возвращает True, если аутентификация пройдена, иначе False.
    """
    details = await db.get_user_details(user.id)
    if not details or not details[7]: # is_verified
        await send_captcha(user.id, state, bot)
        return False
    return True

async def get_user_id_from_input(input_str: str, db: Database) -> int | None:
    """Определяет ID пользователя по username или прямому ID."""
    if input_str.startswith('@'):
        user = await db.get_user_by_username(input_str[1:])
        return user[0] if user else None
    try:
        return int(input_str)
    except ValueError:
        return None