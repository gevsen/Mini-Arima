# app/states.py

from aiogram.fsm.state import State, StatesGroup

class Admin(StatesGroup):
    """Состояния для админ-панели."""
    waiting_for_broadcast = State()
    waiting_for_grant = State()
    waiting_for_revoke = State()
    waiting_for_block = State()
    waiting_for_unblock = State()
    waiting_for_find_user = State()

class ImageGen(StatesGroup):
    """Состояния для генерации изображений."""
    waiting_for_model = State()
    waiting_for_prompt = State()
    
class Captcha(StatesGroup):
    """Состояние для прохождения капчи."""
    waiting_for_answer = State()

class Chat(StatesGroup):
    """Состояние для обычного чата."""
    in_progress = State()

class MaxMode(StatesGroup):
    """Состояние для чата в режиме Max Mode."""
    in_progress = State()

class Settings(StatesGroup):
    """Состояния для меню настроек."""
    waiting_for_instruction = State()
    waiting_for_temperature = State()
