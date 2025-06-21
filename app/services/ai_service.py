# app/services/ai_service.py

import asyncio
import time
import logging
from typing import Tuple, Dict, List

from openai import AsyncOpenAI, APIError
from aiogram.utils.markdown import hcode

from app.config import (
    GLOBAL_SYSTEM_PROMPT, DEFAULT_TEMPERATURE, 
    MAX_MODE_PARTICIPANTS, MAX_MODE_ARBITER
)
from app.services.user_service import get_user_details_cached

logger = logging.getLogger(__name__)

async def get_simple_response(
    ai_client: AsyncOpenAI, 
    model: str, 
    messages: list, 
    user_id: int,
    db,
    cache: Dict 
) -> Tuple[str, float]:
    """
    Получает обычный ответ от одной модели.
    Возвращает кортеж (текст_ответа, время_выполнения).
    В случае ошибки вызывает исключение.
    """
    start_time = time.time()
    
    user_details = await get_user_details_cached(user_id, db, cache)
    user_instruction = user_details[10] if user_details and user_details[10] else None
    user_temperature = user_details[11] if user_details and user_details[11] is not None else DEFAULT_TEMPERATURE

    final_messages = [{"role": "system", "content": GLOBAL_SYSTEM_PROMPT}]
    if user_instruction:
        final_messages.append({"role": "system", "content": f"Дополнительная инструкция от пользователя: {user_instruction}"})
    final_messages.extend(messages)
    
    try:
        logger.debug(f"Requesting model {model} for user {user_id}")
        response = await ai_client.chat.completions.create(
            model=model, messages=final_messages,
            temperature=user_temperature, timeout=120.0
        )
        duration = time.time() - start_time
        
        # --- ИЗМЕНЕНИЕ: Добавлена проверка на None ---
        if not response.choices or response.choices[0].message.content is None:
            logger.warning(f"Model {model} for user {user_id} returned a response with no content. Finish reason: {response.choices[0].finish_reason if response.choices else 'N/A'}")
            # Возвращаем пустую строку, чтобы избежать падений дальше по коду
            return "", duration

        response_text = response.choices[0].message.content
        logger.debug(f"Model {model} for user {user_id} responded in {duration:.2f}s")
        return response_text, duration
    except Exception as e:
        logger.error(f"Failed to get response from model {model} for user {user_id}. Error: {e}", exc_info=True)
        raise

async def _get_participant_response(ai_client, model, prompt, user_id, db, cache):
    """Внутренняя функция для безопасного получения ответа от модели-участника."""
    try:
        response, _ = await get_simple_response(ai_client, model, [{"role": "user", "content": prompt}], user_id, db, cache)
        return model, response
    except Exception as e:
        logger.warning(f"Max Mode participant {model} failed for user {user_id}. Error: {e}")
        return model, f"ОШИБКА: Модель не смогла обработать запрос. ({type(e).__name__})"


# app/services/ai_service.py

# ... (остальной код файла без изменений) ...

async def get_max_mode_response(
    ai_client: AsyncOpenAI,
    prompt: str,
    user_id: int,
    db,
    cache: Dict
) -> Tuple[str, float]:
    """
    Получает ответ в режиме Max Mode: опрашивает несколько моделей
    и передает их ответы модели-арбитру для финального результата.
    """
    full_start_time = time.time()
    logger.info(f"Starting Max Mode for user {user_id}")

    # 1. Параллельно опрашиваем все модели-участники
    tasks = [
        _get_participant_response(ai_client, model_name, prompt, user_id, db, cache)
        for model_name in MAX_MODE_PARTICIPANTS
    ]
    
    participant_results = await asyncio.gather(*tasks)
    logger.info(f"Max Mode participant results for user {user_id}: {participant_results}")

    # 2. Собираем ответы и формируем НОВЫЙ, УЛУЧШЕННЫЙ мета-промпт для арбитра
    # --- ГЛАВНОЕ ИЗМЕНЕНИЕ ЗДЕСЬ ---
    meta_prompt_parts = [
        "Ты — главный AI-арбитр. Твоя задача — проанализировать ответы от нескольких моделей и создать один, наилучший итоговый ответ.",
        "Действуй строго по шагам:",
        "\n**ШАГ 1: Определи правильный ответ.**",
        "Внимательно изучи оригинальный запрос пользователя и все предоставленные ответы. Вычисли или определи единственно верный и точный ответ.",
        "\n**ШАГ 2: Сформируй финальный ответ.**",
        "Напиши исчерпывающий, точный и хорошо отформатированный ответ для пользователя. Используй лучшие идеи и факты из ответов-участников, но изложи их своими словами. Не упоминай другие модели в этой части.",
        "\n**ШАГ 3: Проведи анализ источников.**",
        "После финального ответа поставь разделитель `---`. Затем кратко и объективно проанализируй ответы участников. Укажи, кто был прав, кто ошибся и почему. Твой анализ должен быть полностью консистентен с финальным ответом, который ты дал на ШАГЕ 2.",
        
        f"\n---",
        f"**ОРИГИНАЛЬНЫЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ:**\n{prompt}\n",
        "---",
        "\n**ОТВЕТЫ МОДЕЛЕЙ-УЧАСТНИКОВ ДЛЯ АНАЛИЗА:**"
    ]

    successful_responses = 0
    for model_name, response_text in participant_results:
        safe_response_text = response_text if response_text is not None else "ОШИБКА: Модель не вернула текстовый ответ."
        meta_prompt_parts.append(f"\n**Ответ от модели ({hcode(model_name)}):**\n{safe_response_text}\n---")
        if not safe_response_text.startswith("ОШИБКА:"):
            successful_responses += 1

    # Проверка, есть ли хотя бы один успешный ответ
    if successful_responses == 0:
        logger.error(f"Max Mode failed for user {user_id}: all participants returned an error or empty content.")
        raise RuntimeError("К сожалению, все модели-участники не смогли дать ответ. Попробуйте позже.")

    meta_prompt_parts.append("\n**ТВОЙ ИТОГОВЫЙ РЕЗУЛЬТАТ (выполни ШАГ 2 и ШАГ 3):**")
    meta_prompt = "\n".join(meta_prompt_parts)

    # 3. Отправляем запрос арбитру
    try:
        logger.info(f"Sending meta-prompt to arbiter {MAX_MODE_ARBITER} for user {user_id}")
        final_response_text, _ = await get_simple_response(
            ai_client, MAX_MODE_ARBITER, [{"role": "user", "content": meta_prompt}], user_id, db, cache
        )
    except Exception as e:
        logger.error(f"Max Mode arbiter {MAX_MODE_ARBITER} failed for user {user_id}. Error: {e}")
        raise RuntimeError(f"Модель-арбитр ({MAX_MODE_ARBITER}) не смогла обработать ответы. Попробуйте позже.")

    total_duration = time.time() - full_start_time
    logger.info(f"Max Mode for user {user_id} finished in {total_duration:.2f}s")
    return final_response_text, total_duration

# ... (остальной код файла без изменений) ...