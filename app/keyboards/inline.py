# app/keyboards/inline.py
# Функции для создания инлайн-клавиатур.

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- ИЗМЕНЕНИЕ: импортируем новые классы ---
from app.keyboards.callbacks import (
    Menu, Chat, AdminMenu, AdminUserAction, AdminUserBrowse, ModelCategory, 
    Settings, SelectTextModel, SelectImageModel, SubscriptionDetails,
    Reward, MaxMode
)
from app.config import ADMIN_IDS, SUPPORT_CONTACT, SUB_CONTACT, LIMITS, PRICES
from app.services.user_service import get_user_level

# --- Главные меню ---

async def get_main_menu(user_id: int, db) -> InlineKeyboardMarkup:
    """Формирует главное меню в зависимости от уровня подписки пользователя."""
    builder = InlineKeyboardBuilder()
    user_level = await get_user_level(user_id, db)

    builder.row(InlineKeyboardButton(text='💬 Выбрать модель', callback_data=Menu(action='models').pack()))

    if user_level == 3:
        builder.row(InlineKeyboardButton(text='🚀 Max Mode', callback_data=Menu(action='max_mode').pack()))

    if user_level >= 2:
        builder.row(InlineKeyboardButton(text='🖼️ Создать изображение', callback_data=Menu(action='image_gen').pack()))

    builder.row(
        InlineKeyboardButton(text='⭐ Подписка', callback_data=Menu(action='subscription').pack()),
        InlineKeyboardButton(text='⚙️ Настройки', callback_data=Menu(action='settings').pack())
    )
    builder.row(
        InlineKeyboardButton(text='ℹ️ Помощь', callback_data=Menu(action='help').pack()),
        InlineKeyboardButton(text='🤝 Поддержка', url=f"https://t.me/{SUPPORT_CONTACT}")
    )
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text='👑 Админ-панель', callback_data=Menu(action='admin').pack()))

    return builder.as_markup()

def get_chat_menu(is_max_mode: bool = False) -> InlineKeyboardMarkup:
    """Формирует меню во время диалога."""
    builder = InlineKeyboardBuilder()
    if is_max_mode:
        builder.row(InlineKeyboardButton(text='❌ Выйти из Max Mode', callback_data=MaxMode(action='exit').pack()))
    else:
        builder.row(
            InlineKeyboardButton(text='🔄 Новый чат', callback_data=Chat(action='new').pack()),
            InlineKeyboardButton(text='🔁 Сменить модель', callback_data=Menu(action='models').pack())
        )
    builder.row(InlineKeyboardButton(text='⬅️ Главное меню', callback_data=Chat(action='back_to_main').pack()))
    return builder.as_markup()


# --- Меню Max Mode ---

def get_max_mode_activation_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Активировать Max Mode", callback_data=MaxMode(action="activate").pack())
    builder.button(text="⬅️ Назад", callback_data=Menu(action="back_main").pack())
    builder.adjust(1)
    return builder.as_markup()

# --- Меню выбора моделей ---

def get_models_menu(category: str, models: list, available_statuses: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for model_name in models:
        is_ok = available_statuses.get(model_name, 'OK') == 'OK'
        prefix = "" if is_ok else "⚠️ "
        status = "ok" if is_ok else "failed"
        builder.button(
            text=f"{prefix}{model_name}",
            callback_data=SelectTextModel(model_name=model_name, status=status).pack()
        )
    builder.button(text='⬅️ Назад к категориям', callback_data=Menu(action='models').pack())
    builder.adjust(1)
    return builder.as_markup()

def get_model_categories_menu(categories: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=cat, callback_data=ModelCategory(name=cat).pack())
    builder.button(text='⬅️ Назад в главное меню', callback_data=Menu(action='back_main').pack())
    builder.adjust(2, 2, 2, 1) 
    return builder.as_markup()

def get_image_models_menu(models: list, available_statuses: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for model_name in models:
        is_ok = available_statuses.get(model_name, 'OK') == 'OK'
        prefix = "" if is_ok else "⚠️ "
        status = "ok" if is_ok else "failed"
        builder.button(
            text=f"{prefix}{model_name}",
            callback_data=SelectImageModel(model_name=model_name, status=status).pack()
        )
    builder.button(text='⬅️ Назад в главное меню', callback_data=Menu(action='back_main').pack())
    builder.adjust(1)
    return builder.as_markup()


# --- Меню подписок и настроек ---

def get_subscription_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='Подробнее о Standard', callback_data=SubscriptionDetails(level=1).pack())
    builder.button(text='Подробнее о Premium', callback_data=SubscriptionDetails(level=2).pack())
    builder.button(text='Подробнее о Max', callback_data=SubscriptionDetails(level=3).pack())
    builder.button(text='⬅️ Назад', callback_data=Menu(action='back_main').pack())
    builder.adjust(1)
    return builder.as_markup()

def get_subscription_details_menu(level: int) -> InlineKeyboardMarkup:
    plan_name = {1: "Standard", 2: "Premium", 3: "Max"}[level]
    price = PRICES[level]
    buy_text = f"Здравствуйте, хочу купить подписку {plan_name}."

    builder = InlineKeyboardBuilder()
    builder.button(
        text=f'Купить {plan_name} - {price}₽',
        url=f"https://t.me/{SUB_CONTACT}?text={buy_text}"
    )
    builder.button(text='⬅️ Назад', callback_data=Menu(action='subscription').pack())
    builder.adjust(1)
    return builder.as_markup()

def get_reward_menu(channels: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, channel in enumerate(channels):
        url = f"https://t.me/{channel['id'].lstrip('@')}"
        builder.row(InlineKeyboardButton(text=f"Канал {i+1}: {channel['name']}", url=url))
    builder.row(InlineKeyboardButton(text="✅ Я подписался, проверить!", callback_data=Reward(action="check").pack()))
    return builder.as_markup()

def get_settings_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Задать инструкцию", callback_data=Settings(action="instruction").pack())
    builder.button(text="Задать температуру", callback_data=Settings(action="temperature").pack())
    builder.button(text="⬅️ Назад", callback_data=Menu(action="back_main").pack())
    builder.adjust(1)
    return builder.as_markup()


# --- Админ-меню (ОБНОВЛЕНО) ---

def get_admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # level=0 - главное меню админки
    builder.button(text='📊 Статистика', callback_data=AdminMenu(level=0, action='stats').pack())
    builder.button(text='👥 Пользователи', callback_data=AdminMenu(level=0, action='users').pack())
    builder.button(text='📣 Рассылка', callback_data=AdminMenu(level=0, action='broadcast').pack())
    builder.button(text='🩺 Отчёт о моделях', callback_data=AdminMenu(level=0, action='report').pack())
    builder.button(text='⬅️ Назад', callback_data=Menu(action='back_main').pack())
    builder.adjust(2, 2, 1)
    return builder.as_markup()

def get_admin_users_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # level=1 - меню управления пользователями
    builder.button(text='🔍 Найти пользователя', callback_data=AdminMenu(level=1, action='find_user').pack())
    builder.button(text='📜 Просмотр пользователей', callback_data=AdminUserBrowse(page=1).pack())
    builder.button(text='🎁 Выдать подписку', callback_data=AdminMenu(level=1, action='grant').pack())
    # Кнопки "Забрать подписку", "Блокировка", "Разблокировка" удалены,
    # так как эти действия выполняются из карточки пользователя.
    builder.button(text='⬅️ Назад', callback_data=AdminMenu(level=1, action='back').pack())
    builder.adjust(2, 1)
    return builder.as_markup()

def get_user_card_menu(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    block_action = 'unblock' if is_blocked else 'block'
    block_text = '✅ Разблокировать' if is_blocked else '🚫 Заблокировать'
    
    builder.button(text=block_text, callback_data=AdminUserAction(user_id=user_id, action=block_action).pack())
    builder.button(text="🗑️ Забрать подписку", callback_data=AdminUserAction(user_id=user_id, action='revoke').pack())
    # Эта кнопка ведет в меню управления пользователями
    builder.button(text="⬅️ К управлению", callback_data=AdminMenu(level=0, action='users').pack())
    builder.adjust(1)
    return builder.as_markup()

def get_user_browse_menu(page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️", callback_data=AdminUserBrowse(page=page - 1).pack()))
    if page < total_pages:
        buttons.append(InlineKeyboardButton(text="➡️", callback_data=AdminUserBrowse(page=page + 1).pack()))

    if buttons:
        builder.row(*buttons)

    # Эта кнопка ведет в меню управления пользователями
    builder.row(InlineKeyboardButton(text="⬅️ К управлению", callback_data=AdminMenu(level=0, action='users').pack()))
    return builder.as_markup()

def get_back_to_admin_menu() -> InlineKeyboardMarkup:
    # Эта кнопка ведет в главное меню админки
    return InlineKeyboardBuilder().button(text='⬅️ Назад', callback_data=AdminMenu(level=1, action='back').pack()).as_markup()