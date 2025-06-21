# app/middlewares.py
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from cachetools import TTLCache

class ThrottlingMiddleware(BaseMiddleware):
    """
    Простое middleware для защиты от флуда.
    """
    def __init__(self, rate_limit: float = 1.0, key_prefix: str = "antiflood_"):
        # TTLCache хранит записи с определенным временем жизни (ttl)
        # Мы используем его как хранилище "последней активности" пользователя
        self.cache = TTLCache(maxsize=10_000, ttl=rate_limit)
        self.rate_limit = rate_limit
        self.key_prefix = key_prefix

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Пытаемся получить пользователя из данных, которые передает aiogram
        user: User | None = data.get("event_from_user")

        if user:
            cache_key = f"{self.key_prefix}{user.id}"

            # Если ключ уже есть в кэше, значит, пользователь отправляет сообщения слишком часто
            if cache_key in self.cache:
                # Игнорируем событие, не передавая его дальше по цепочке обработчиков
                return
            else:
                # Если ключа нет, добавляем его в кэш. Он автоматически удалится через `rate_limit` секунд.
                self.cache[cache_key] = None
        
        # Если все в порядке, передаем событие дальше
        return await handler(event, data)
