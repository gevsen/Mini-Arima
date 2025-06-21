# app/keyboards/callbacks.py
# Определяем все классы CallbackData для проекта.

from aiogram.filters.callback_data import CallbackData

# --- Общие ---
class Menu(CallbackData, prefix="menu"):
    action: str

class Reward(CallbackData, prefix="reward"):
    action: str

class SubscriptionDetails(CallbackData, prefix="sub_details"):
    level: int

# --- Чат и Модели ---
class Chat(CallbackData, prefix="chat"):
    action: str

class MaxMode(CallbackData, prefix="max_mode"):
    action: str

class ModelCategory(CallbackData, prefix="cat"):
    name: str

class SelectTextModel(CallbackData, prefix="model"):
    model_name: str
    status: str

class SelectImageModel(CallbackData, prefix="img_model"):
    model_name: str
    status: str

# --- Настройки ---
class Settings(CallbackData, prefix="settings"):
    action: str

# --- НОВЫЕ, БОЛЕЕ КОНКРЕТНЫЕ КЛАССЫ ДЛЯ АДМИНКИ ---

# Для кнопок в главном меню админки и меню управления пользователями
class AdminMenu(CallbackData, prefix="adm"):
    # level: 0 - главное меню, 1 - меню пользователей
    # action: stats, users, broadcast, report, back, find_user, grant
    level: int
    action: str

# Для действий над конкретным пользователем из его карточки
class AdminUserAction(CallbackData, prefix="adm_user"):
    # action: block, unblock, revoke
    user_id: int
    action: str
    
# Для постраничного просмотра пользователей
class AdminUserBrowse(CallbackData, prefix="adm_browse"):
    page: int