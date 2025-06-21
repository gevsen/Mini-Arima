# app/config.py

import os
from dotenv import load_dotenv
from datetime import timezone, timedelta

load_dotenv()

# --- Временная зона ---
MSK_TZ = timezone(timedelta(hours=3))

# --- Основные настройки ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_KEY = os.getenv('API_KEY')
API_URL = os.getenv('API_URL')
DATABASE_PATH = os.getenv('DATABASE', 'database.db')

# --- Администраторы и контакты ---
ADMIN_IDS_STR = os.getenv('ADMIN_IDS')
if not ADMIN_IDS_STR:
    raise ValueError("ADMIN_IDS не найден в .env файле! Бот не может быть запущен.")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',')]

SUB_CONTACT = os.getenv('SUB_CONTACT', 'gevsen')
SUPPORT_CONTACT = os.getenv('SUPPORT_CONTACT', 'gevsen')


# --- Настройки наград и групп ---
REWARD_CHANNELS = [
    {'id': os.getenv('REWARD_CHANNEL_1_ID'), 'name': os.getenv('REWARD_CHANNEL_1_NAME')},
    {'id': os.getenv('REWARD_CHANNEL_2_ID'), 'name': os.getenv('REWARD_CHANNEL_2_NAME')}
]
REWARD_CHANNELS = [ch for ch in REWARD_CHANNELS if ch['id'] and ch['name']]

GROUP_TEXT_TRIGGER = os.getenv('GROUP_TEXT_TRIGGER', '.text')
GROUP_IMAGE_TRIGGER = os.getenv('GROUP_IMAGE_TRIGGER', '.image')


# --- Настройки моделей и AI ---
GLOBAL_SYSTEM_PROMPT = "Ты - MiniArima, продвинутый GenAI ассистент."
DEFAULT_TEMPERATURE = 0.7
DEFAULT_TEXT_MODEL = 'chatgpt-4o-latest'
DEFAULT_IMAGE_MODEL = 'gpt-image-1'


# --- Настройки Max Mode ---
MAX_MODE_PARTICIPANTS = ['grok-3', 'gpt-4.1', 'deepseek-chat-v3-0324', 'gpt-4.5-preview', 'chatgpt-4o-latest', 'claude-3.7-sonnet']
MAX_MODE_ARBITER = 'deepseek-r1-0528'


# --- Модели и уровни доступа (ИЗМЕНЕНО) ---
MODEL_CATEGORIES = {
    'OpenAI': ['gpt-4.5-preview', 'gpt-4.1', 'o4-mini', 'chatgpt-4o-latest'], # Убрали o1-pro
    'DeepSeek': ['deepseek-chat-v3-0324', 'deepseek-r1-0528'],
    'Meta': ['llama-3.1-nemotron-ultra-253b-v1'],
    'Alibaba': ['qwen3-235b-a22b'],
    # 'Google': ['gemini-2.5-pro-exp-03-25'], # Полностью убрали категорию Google
    'Microsoft': ['phi-4-reasoning-plus'],
    'xAI': ['grok-3', 'grok-3-mini'],
    'Anthropic': ['claude-3.7-sonnet']
}
MODELS = {
    'free': ['deepseek-chat-v3-0324', 'gpt-4.1', 'chatgpt-4o-latest'],
    'standard': ['deepseek-chat-v3-0324', 'gpt-4.1', 'chatgpt-4o-latest', 'llama-3.1-nemotron-ultra-253b-v1', 'qwen3-235b-a22b', 'phi-4-reasoning-plus', 'grok-3-mini'],
    'premium': list(set(p for cat in MODEL_CATEGORIES.values() for p in cat)) # Все модели доступны
}
IMAGE_MODELS = ['gpt-image-1', 'flux-1.1-pro']


# --- Лимиты и подписки ---
# Уровни: 0=Free, 1=Standard, 2=Premium, 3=Max
LIMITS = {
    0: {"daily": 3, "max_mode": 0},
    1: {"daily": 40, "max_mode": 0},
    2: {"daily": 100, "max_mode": 0},
    3: {"daily": 100, "max_mode": 5}
}
REWARD_LIMIT = 7 # Бонусный лимит для Free-пользователей
PRICES = {1: 150, 2: 350, 3: 600} # Цены для Standard, Premium, Max


# --- Капча ---
CAPTCHA_VARIANTS = [
    ("Чему равен корень из 9?", "3"),
    ("Сколько будет 2 + 2 * 2?", "6"),
    ("Столица Франции?", "париж"),
    ("Сколько букв в слове 'ТЕЛЕГРАМ'?", "8"),
    ("Напишите число 'пять' цифрой.", "5")
]