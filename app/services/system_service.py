# app/services/system_service.py
# Логика, связанная с состоянием системы, например, проверка моделей.

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List

import aiohttp
from openai import AsyncOpenAI, APIError
from aiogram.utils.markdown import hcode

# --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
# Убираем локальное создание MSK_TZ и импортируем его из config
from app.config import (
    MODEL_CATEGORIES, IMAGE_MODELS, MAX_MODE_PARTICIPANTS, MAX_MODE_ARBITER,
    API_URL, API_KEY, MSK_TZ
)

logger = logging.getLogger(__name__)

# --- Функции проверки моделей ---

async def test_chat_model(ai_client: AsyncOpenAI, model: str) -> dict:
    """Тестирует доступность текстовой модели."""
    try:
        await ai_client.chat.completions.create(
            model=model, messages=[{'role': 'user', 'content': 'Test'}],
            temperature=0.7, max_tokens=10, timeout=20.0
        )
        return {'model': model, 'status': 'OK'}
    except APIError as e:
        logger.warning(f"Model {model} test failed with APIError: {e.status_code}")
        return {'model': model, 'status': f'API Error {e.status_code}'}
    except asyncio.TimeoutError:
        logger.warning(f"Model {model} test timed out.")
        return {'model': model, 'status': 'Timeout'}
    except Exception as e:
        logger.error(f"Model {model} test failed with unexpected error: {e}", exc_info=True)
        return {'model': model, 'status': f'Error: {type(e).__name__}'}

async def test_image_model(model: str) -> dict:
    """Тестирует доступность модели для генерации изображений."""
    async with aiohttp.ClientSession() as session:
        url = f"{API_URL}/images/generations"
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "prompt": "Test", "height": 512, "width": 512, "n": 1, "response_format": "url"}
        try:
            async with session.post(url, headers=headers, json=payload, timeout=45) as response:
                if response.status == 200:
                    return {'model': model, 'status': 'OK'}
                else:
                    logger.warning(f"Image model {model} test failed with status {response.status}")
                    return {'model': model, 'status': f'Error {response.status}'}
        except asyncio.TimeoutError:
            logger.warning(f"Image model {model} test timed out.")
            return {'model': model, 'status': 'Timeout'}
        except Exception as e:
            logger.error(f"Image model {model} test failed with unexpected error: {e}", exc_info=True)
            return {'model': model, 'status': f'Error: {type(e).__name__}'}

# --- Основные функции управления состоянием ---

def is_model_available(model_name: str, cache: Dict) -> bool:
    """Проверяет, доступна ли модель, по данным из кэша."""
    model_status_cache = cache.get("model_status")
    if model_status_cache is None: return True # Если кэша нет, считаем доступной

    statuses = model_status_cache.get("statuses", {})
    return statuses.get(model_name, 'OK') == 'OK'

def are_max_mode_models_available(cache: Dict) -> bool:
    """Проверяет, доступны ли ВСЕ модели, необходимые для Max Mode."""
    required_models = MAX_MODE_PARTICIPANTS + [MAX_MODE_ARBITER]
    for model in required_models:
        if not is_model_available(model, cache):
            logger.warning(f"Max Mode is unavailable because model '{model}' is down.")
            return False
    return True

def set_model_failed_in_cache(model_name: str, cache: Dict):
    """Помечает модель как недоступную в кэше до следующей проверки."""
    model_status_cache = cache.get("model_status")
    if model_status_cache is not None:
        statuses = model_status_cache.get("statuses", {})
        if statuses.get(model_name) != 'FAILED':
            statuses[model_name] = 'FAILED'
            model_status_cache["statuses"] = statuses
            logger.warning(f"Circuit Breaker: Model {model_name} marked as FAILED in cache due to runtime error.")

async def scheduled_model_test(ai_client: AsyncOpenAI, db, cache: Dict):
    """
    Запланированная задача для проверки всех моделей и обновления их статуса.
    """
    logger.info("Running scheduled model health check...")

    all_text_models = list(set(model for models in MODEL_CATEGORIES.values() for model in models))
    all_image_models = list(set(IMAGE_MODELS))

    tasks = [test_chat_model(ai_client, m) for m in all_text_models]
    tasks.extend([test_image_model(m) for m in all_image_models])

    results = await asyncio.gather(*tasks)

    current_statuses = {r['model']: r['status'] for r in results}

    # Формируем отчет
    working_models = sorted([r['model'] for r in results if r['status'] == 'OK'])
    failed_models = sorted([(r['model'], r['status']) for r in results if r['status'] != 'OK'], key=lambda x: x[0])

    timestamp = datetime.now(MSK_TZ).strftime('%d.%m.%Y %H:%M:%S МСК')
    report_text = f"<b>Отчёт о состоянии моделей от {timestamp}</b>\n\n"
    if working_models:
        report_text += f"<b>✅ Рабочие модели ({len(working_models)}):</b>\n" + "\n".join(f"  •  {hcode(m)}" for m in working_models)
    if failed_models:
        report_text += f'\n\n<b>❌ Нерабочие модели ({len(failed_models)}):</b>\n' + "\n".join(f"  •  {hcode(m)} - {s}" for m, s in failed_models)

    # Обновляем кэш и БД
    model_status_cache = cache.get("model_status")
    if model_status_cache is not None:
        model_status_cache["statuses"] = current_statuses
        model_status_cache["last_report"] = report_text

    await db.set_system_state('model_status', json.dumps(current_statuses))
    await db.set_system_state('last_report', report_text)

    logger.info("Scheduled model health check finished. State saved to cache and DB.")


async def startup_model_check(ai_client: AsyncOpenAI, db, cache: Dict):
    """
    Проверка статуса моделей при запуске бота.
    Сначала пытается загрузить свежие данные из БД, если их нет - запускает полную проверку.
    """
    logger.info("Performing startup model check...")
    model_status_cache = cache.get("model_status")

    status_state = await db.get_system_state('model_status')
    report_state = await db.get_system_state('last_report')

    if status_state and report_state:
        status_json, status_timestamp_str = status_state
        try:
            status_timestamp = datetime.fromisoformat(status_timestamp_str)
            # Если данные в БД "свежие" (меньше 10 минут)
            if datetime.now(timezone.utc) - status_timestamp < timedelta(minutes=10):
                if model_status_cache is not None:
                    model_status_cache["statuses"] = json.loads(status_json)
                    model_status_cache["last_report"] = report_state[0]
                logger.info("Loaded recent model status from database. Skipping initial full check.")
                return
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            logger.warning(f"Could not parse state from DB ({e}), running full check.")

    # Если свежих данных в БД нет, запускаем полную проверку
    logger.info("No fresh model status in DB. Running full health check...")
    await scheduled_model_test(ai_client, db, cache)