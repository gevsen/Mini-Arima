# bot.py (в корне проекта)

import asyncio
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject, CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import AsyncOpenAI
from cachetools import TTLCache

# Импорты из нашей новой структуры
from app.config import BOT_TOKEN, API_KEY, API_URL, DATABASE_PATH
from app.database import Database
from app.middlewares import ThrottlingMiddleware
# --- ИЗМЕНЕНИЕ: добавляем group ---
from app.handlers import admin, chat, common, image_gen, settings, subscription, group
from app.services.system_service import scheduled_model_test, startup_model_check

# Глобальные переменные и объекты
logger = logging.getLogger(__name__)

# Глобальный кэш для хранения данных, например, статуса моделей
GLOBAL_CACHE = {
    "model_status": TTLCache(maxsize=1, ttl=600),
    "user_details": TTLCache(maxsize=1000, ttl=300) # Кэш для данных пользователей
}

# --- MIDDLEWARE ДЛЯ ЛОГИРОВАНИЯ ---
class LoggingMiddleware(BaseMiddleware):
    """
    Middleware для логирования входящих CallbackQuery.
    Помогает в отладке, показывая, какие данные приходят от кнопок.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Логируем только CallbackQuery для чистоты логов
        if isinstance(event, CallbackQuery):
            logger.info(f"--> Incoming CallbackQuery: data='{event.data}' from user_id={event.from_user.id}")
        return await handler(event, data)

async def set_bot_commands(bot_instance: Bot):
    """Устанавливает команды, видимые в меню Telegram."""
    commands = [
        BotCommand(command="start", description="Перезапустить бота / Главное меню"),
        BotCommand(command="menu", description="Показать меню"),
    ]
    await bot_instance.set_my_commands(commands)

async def main():
    """Основная функция для запуска бота."""
    # Настраиваем логирование в файл и в консоль для отладки
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        handlers=[
            logging.FileHandler("bot.log", mode='a'), # Запись в файл (дозапись)
            logging.StreamHandler()                    # Вывод в консоль
        ]
    )
    logger.info("Starting bot...")

    # Инициализация основных объектов
    storage = MemoryStorage()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=storage)
    db = Database(DATABASE_PATH)
    ai_client = AsyncOpenAI(base_url=API_URL, api_key=API_KEY)
    
    # Инициализация планировщика
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    # Передача зависимостей в диспетчер
    dp["db"] = db
    dp["ai_client"] = ai_client
    dp["scheduler"] = scheduler
    dp["cache"] = GLOBAL_CACHE

    # Настройка middleware
    dp.update.middleware(LoggingMiddleware())
    dp.update.middleware(ThrottlingMiddleware(rate_limit=1.0))

    # Регистрация роутеров из модулей handlers
    logger.info("Registering routers...")
    dp.include_router(common.router)
    dp.include_router(subscription.router)
    dp.include_router(settings.router)
    dp.include_router(image_gen.router)
    dp.include_router(admin.router)
    # --- ИЗМЕНЕНИЕ: добавляем роутер для групп ---
    dp.include_router(group.router)
    dp.include_router(chat.router) # Роутер для личных сообщений должен идти последним

    # Инициализация базы данных
    await db.init_db()
    
    # Запускаем проверку моделей как фоновую задачу
    logger.info("Scheduling startup model check to run in the background.")
    asyncio.create_task(startup_model_check(ai_client, db, GLOBAL_CACHE))

    # Настройка и запуск фоновой задачи для регулярной проверки моделей
    scheduler.add_job(
        scheduled_model_test, 
        'interval', 
        minutes=10, 
        args=(ai_client, db, GLOBAL_CACHE)
    )
    scheduler.start()

    # Установка команд бота
    await set_bot_commands(bot)

    # Запуск polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        scheduler.shutdown()
        logger.info("Bot stopped.")

if __name__ == '__main__':
    # Создаем логгер для этого блока, чтобы точно записать критическую ошибку
    main_logger = logging.getLogger(__name__)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        main_logger.info("Bot execution stopped manually.")
    except Exception as e:
        # Эта ловушка поймает любое исключение, которое пытается обрушить бот
        main_logger.critical("!!! CRITICAL ERROR - BOT STOPPED !!!", exc_info=True)