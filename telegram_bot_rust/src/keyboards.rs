// src/keyboards.rs

use std::sync::Arc;
use teloxide::types::{InlineKeyboardMarkup, InlineKeyboardButton, InlineKeyboardButtonKind};
use crate::db::Database; // For accessing DB if keyboard structure depends on user state
use crate::config::CONFIG; // For accessing model lists, etc.

// --- Callback Data Structures ---
// It's good practice to define callback data as enums or structs for type safety.
// Example:
// #[derive(Serialize, Deserialize, Debug, Clone)] // If using serde for callback data
// pub enum CallbackData {
//     OpenSettings,
//     SelectTextModel(String),
//     SelectImageModel(String),
//     // ... other actions
// }
// For simplicity now, we might use simple strings, but structured data is better.
// Teloxide's `teloxide::dispatching::dialogue::CallbackData` can also be used with dptree.

// --- Main Menu Keyboard ---
pub async fn create_main_menu_keyboard(_user_id: i64, _db: &Arc<Database>) -> InlineKeyboardMarkup {
    // In the future, this keyboard might change based on user's subscription or status
    let mut keyboard: Vec<Vec<InlineKeyboardButton>> = Vec::new();

    keyboard.push(vec![
        InlineKeyboardButton::callback("📝 Начать чат", "start_chat"),
        InlineKeyboardButton::callback("🖼️ Генерация изображений", "generate_image_menu"),
    ]);
    keyboard.push(vec![
        InlineKeyboardButton::callback("⚙️ Настройки", "settings_menu"),
        InlineKeyboardButton::callback("👑 Подписка", "subscription_menu"),
    ]);
    keyboard.push(vec![
        InlineKeyboardButton::callback("ℹ️ Помощь", "help_info"),
        // Example: Admin button shown only to admins
        // if CONFIG.admin_ids.contains(&user_id) {
        //     keyboard.last_mut().unwrap().push(InlineKeyboardButton::callback("🛠️ Админ-панель", "admin_panel"));
        // }
    ]);

    // If user is an admin, add admin panel button
    // This requires user_id and checking against CONFIG.admin_ids
    // For now, let's assume this check happens before calling or is passed as a boolean
    // if is_admin {
    //    keyboard.push(vec![InlineKeyboardButton::callback("👑 Админ-панель", "admin_panel")]);
    // }


    InlineKeyboardMarkup::new(keyboard)
}

// --- Settings Menu Keyboard ---
pub fn create_settings_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("Системная инструкция", "set_user_instruction"),
            InlineKeyboardButton::callback("Температура", "set_temperature"),
        ],
        vec![
            InlineKeyboardButton::callback("Выбрать модель текста", "select_text_model_menu"),
          //  InlineKeyboardButton::callback("Выбрать модель изображений", "select_image_model_menu"), // Covered by image_gen_menu
        ],
        vec![InlineKeyboardButton::callback("🔙 Назад в главное меню", "main_menu")],
    ])
}


// --- Text Model Selection Keyboard ---
pub fn create_text_model_selection_keyboard(current_model: Option<&str>) -> InlineKeyboardMarkup {
    let mut keyboard: Vec<Vec<InlineKeyboardButton>> = Vec::new();
    let mut row: Vec<InlineKeyboardButton> = Vec::new();

    // Flatten all models from categories for selection.
    // In a real scenario, you'd filter based on user's subscription level.
    let mut all_models: Vec<String> = CONFIG.model_categories.values()
        .flat_map(|models| models.iter().cloned())
        .collect::<std::collections::HashSet<_>>() // Unique models
        .into_iter()
        .collect();
    all_models.sort(); // Consistent order

    for model_name in all_models {
        let display_name = if Some(model_name.as_str()) == current_model {
            format!("✅ {}", model_name)
        } else {
            model_name.clone()
        };
        row.push(InlineKeyboardButton::callback(display_name, format!("set_text_model:{}", model_name)));
        if row.len() == 2 { // Max 2 buttons per row
            keyboard.push(row);
            row = Vec::new();
        }
    }
    if !row.is_empty() {
        keyboard.push(row);
    }
    keyboard.push(vec![InlineKeyboardButton::callback("🔙 Назад в настройки", "settings_menu")]);
    InlineKeyboardMarkup::new(keyboard)
}

// --- Image Generation Menu Keyboard ---
pub fn create_image_generation_menu_keyboard(current_image_model: Option<&str>) -> InlineKeyboardMarkup {
     let mut keyboard: Vec<Vec<InlineKeyboardButton>> = Vec::new();
     let mut row: Vec<InlineKeyboardButton> = Vec::new();

    for model_name in &CONFIG.image_models {
         let display_name = if Some(model_name.as_str()) == current_image_model {
            format!("✅ {}", model_name)
        } else {
            model_name.clone()
        };
        row.push(InlineKeyboardButton::callback(display_name, format!("set_image_model:{}", model_name)));
        if row.len() == 1 { // One model per row for image models, or adjust as needed
            keyboard.push(row);
            row = Vec::new();
        }
    }
    if !row.is_empty() {
        keyboard.push(row);
    }
    keyboard.push(vec![
        InlineKeyboardButton::callback("🖼️ Сгенерировать (с текущей моделью)", "generate_image_action"),
    ]);
    keyboard.push(vec![InlineKeyboardButton::callback("🔙 Назад в главное меню", "main_menu")]);
    InlineKeyboardMarkup::new(keyboard)
}


// --- Subscription Menu Keyboard ---
pub fn create_subscription_menu_keyboard(user_level: i32, sub_end_date: Option<String>) -> InlineKeyboardMarkup {
    let mut keyboard = vec![];

    let status_text = match user_level {
        0 => "Уровень: Free".to_string(),
        1 => format!("Уровень: Standard (до {})", sub_end_date.unwrap_or_else(|| "N/A".into())),
        2 => format!("Уровень: Premium (до {})", sub_end_date.unwrap_or_else(|| "N/A".into())),
        3 => format!("Уровень: Max (до {})", sub_end_date.unwrap_or_else(|| "N/A".into())),
        _ => "Неизвестный уровень".to_string(),
    };
    // Button that shows current status - not clickable or callback to "self"
    keyboard.push(vec![InlineKeyboardButton::callback(status_text, "sub_status_info")]);


    if user_level < 1 { // Free
        keyboard.push(vec![InlineKeyboardButton::callback(format!("Купить Standard ({руб}₽)", руб = CONFIG.prices.get(&1).unwrap_or(&0)), "buy_sub:1")]);
    }
    if user_level < 2 { // Free or Standard
        keyboard.push(vec![InlineKeyboardButton::callback(format!("Купить Premium ({руб}₽)", руб = CONFIG.prices.get(&2).unwrap_or(&0)), "buy_sub:2")]);
    }
     if user_level < 3 { // Free, Standard, or Premium
        keyboard.push(vec![InlineKeyboardButton::callback(format!("Купить Max ({руб}₽)", руб = CONFIG.prices.get(&3).unwrap_or(&0)), "buy_sub:3")]);
    }

    // TODO: Add "Продлить подписку" if user has one and it's expiring soon
    // TODO: Add "Управление автоплатежом" if such feature exists

    keyboard.push(vec![InlineKeyboardButton::callback("🎁 Ввести промокод", "enter_promocode")]);
    keyboard.push(vec![InlineKeyboardButton::callback("🔙 Назад в главное меню", "main_menu")]);

    InlineKeyboardMarkup::new(keyboard)
}

// --- Admin Panel Keyboard ---
pub fn create_admin_panel_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("📊 Статистика", "admin_stats"),
            InlineKeyboardButton::callback("✉️ Рассылка", "admin_broadcast"),
        ],
        vec![
            InlineKeyboardButton::callback("👤 Управление пользователем", "admin_manage_user"),
            // InlineKeyboardButton::callback("⚙️ Управление моделями", "admin_manage_models"), // If needed
        ],
        vec![InlineKeyboardButton::callback("🔙 Назад в главное меню", "main_menu")],
    ])
}

// Add more keyboard generation functions as needed for other handlers (e.g., admin, settings details)

// Example of a simple confirmation keyboard
pub fn confirm_action_keyboard(action_callback: &str, cancel_callback: &str) -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("✅ Да", format!("confirm:{}", action_callback)),
        InlineKeyboardButton::callback("❌ Нет", format!("cancel:{}", cancel_callback)),
    ]])
}
