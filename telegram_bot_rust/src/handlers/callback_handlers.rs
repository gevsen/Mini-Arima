// src/handlers/callback_handlers.rs

use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{CallbackQuery, InlineKeyboardMarkup, ParseMode};

use crate::db;
use crate::keyboards;
use crate::user_service::AppCache;
use crate::config::CONFIG;
use crate::states::{MyDialogue, State}; // Import MyDialogue and State

// Renamed to reflect it's part of the dialogue system
pub async fn handle_callback_query_dialogue(
    bot: Bot,
    dialogue: MyDialogue,
    q: CallbackQuery, // Renamed from query to q to match common teloxide examples
    db_pool: Arc<db::Database>,
    _app_cache: Arc<tokio::sync::Mutex<AppCache>>, // Keep if needed, or remove if not used by these callbacks
) -> anyhow::Result<()> { // Changed to anyhow::Result
    let user_id = q.from.id.0 as i64;

    // Ensure there's a message associated with the callback query
    let original_message = match q.message.as_ref() {
        Some(msg) => msg,
        None => {
            log::warn!("Callback query for user {} has no associated message. Ignoring.", user_id);
            bot.answer_callback_query(q.id.clone()).text("Error: Original message not found.").show_alert(true).await?;
            return Ok(());
        }
    };

    if let Some(data) = q.data.as_ref() {
        log::info!("Received callback query with data: '{}' from user {}", data, user_id);

        // --- Route callback data ---
        match data.as_str() {
            "main_menu" => {
                let menu_text = "🤖 <b>Главное меню</b>\n\nВыберите действие:";
                let keyboard = keyboards::create_main_menu_keyboard(user_id, &db_pool).await;
                bot.edit_message_text(original_message.chat.id, original_message.id, menu_text)
                    .reply_markup(keyboard)
                    .parse_mode(ParseMode::Html)
                    .await?;
                dialogue.update(State::MainMenu).await?; // Ensure dialogue state is consistent
                bot.answer_callback_query(q.id.clone()).await?;
            }
            "start_chat" => {
                // TODO: Transition to chat state/dialogue properly
                bot.edit_message_text(original_message.chat.id, original_message.id, "Начинаем чат... (функционал в разработке).\n\nВведите ваш вопрос:").await?;
                // dialogue.update(State::ActiveChat { history: vec![], current_model: "default".to_string() }).await?;
                bot.answer_callback_query(q.id.clone()).text("Переход в режим чата...").await?; // Acknowledge first
                // Then, edit the message and update dialogue state
                // Fetch the default/last used text model for the user
                let user_db_details = db_pool.get_user(user_id).await.ok().flatten();
                let chat_model = user_db_details
                    .and_then(|u| u.last_used_model)
                    .unwrap_or_else(|| CONFIG.default_text_model.clone());

                bot.edit_message_text(
                    original_message.chat.id,
                    original_message.id,
                    format!("🎙️ <b>Режим чата активирован</b> (Модель: <code>{}</code>)\n\nВведите ваше сообщение. Для выхода из чата и возврата в меню, отправьте /menu или /stopchat.", &chat_model)
                )
                .reply_markup(InlineKeyboardMarkup::new(vec![vec![ // Simple keyboard to exit chat
                    InlineKeyboardButton::callback("🔙 Выйти из чата", "exit_chat_to_main_menu"),
                ]]))
                .parse_mode(ParseMode::Html)
                .await?;

                dialogue.update(State::ActiveChat {
                    history: Vec::new(), // Start with empty history
                    current_model: chat_model,
                }).await?;
            }
            "exit_chat_to_main_menu" => { // Callback from the "Выйти из чата" button
                let menu_text = "🤖 <b>Главное меню</b>\n\nВыберите действие:";
                let keyboard = keyboards::create_main_menu_keyboard(user_id, &db_pool).await;
                bot.edit_message_text(original_message.chat.id, original_message.id, menu_text)
                    .reply_markup(keyboard)
                    .parse_mode(ParseMode::Html)
                    .await?;
                dialogue.update(State::MainMenu).await?;
                bot.answer_callback_query(q.id.clone()).text("Выход из режима чата.").await?;
            }
            "generate_image_menu" => {
                let current_model = db_pool.get_user(user_id).await.ok().flatten().and_then(|u| u.last_used_image_model);
                let text = "🖼️ <b>Генерация изображений</b>\n\nВыберите модель или нажмите 'Сгенерировать'.";
                let keyboard = keyboards::create_image_generation_menu_keyboard(current_model.as_deref());
                bot.edit_message_text(original_message.chat.id, original_message.id, text)
                    .reply_markup(keyboard)
                    .parse_mode(ParseMode::Html)
                    .await?;
                // dialogue.update(State::ImageMenuState) // Or similar if specific state needed
                bot.answer_callback_query(q.id.clone()).await?;
            }
            "settings_menu" => {
                let text = "⚙️ <b>Настройки</b>\n\nЗдесь вы можете настроить бота под себя.";
                let keyboard = keyboards::create_settings_keyboard();
                bot.edit_message_text(original_message.chat.id, original_message.id, text)
                    .reply_markup(keyboard)
                    .parse_mode(ParseMode::Html)
                    .await?;
                // dialogue.update(State::SettingsMenuState) // Or similar
                dialogue.update(State::SettingsMenu).await?; // Explicitly go to SettingsMenu state
                bot.answer_callback_query(q.id.clone()).await?;
            }
            // --- Callback for initiating user instruction setting ---
            "set_user_instruction" => {
                let prompt_message = bot.send_message(
                    original_message.chat.id,
                    "Введите вашу системную инструкцию (например, 'Отвечай всегда в стиле пирата'):"
                ).await?;
                dialogue.update(State::WaitingUserSettingsInstruction {
                    original_message_id_to_delete: Some(prompt_message.id.0)
                }).await?;
                // We might want to delete the message with the settings keyboard or edit it.
                // For now, just sending a new prompt.
                // Consider deleting original_message if it's the settings menu itself.
                // bot.delete_message(original_message.chat.id, original_message.id).await?;
                bot.answer_callback_query(q.id.clone()).await?;
            }
            // --- Callback for initiating user temperature setting ---
            "set_temperature" => {
                // Fetch current temperature to display
                let current_temp_str = match db_pool.get_user(user_id).await.ok().flatten().and_then(|u| u.user_temperature) {
                    Some(t) => format!("Текущее значение: {:.1}", t),
                    None => format!("Текущее значение: По умолчанию ({:.1})", CONFIG.default_temperature),
                };

                let prompt_message = bot.send_message(
                    original_message.chat.id,
                    format!("Введите желаемую температуру (число от 0.0 до 2.0, например, 0.7).\n{}", current_temp_str)
                ).await?;
                dialogue.update(State::WaitingUserSettingsTemperature {
                    original_message_id_to_delete: Some(prompt_message.id.0)
                }).await?;
                bot.answer_callback_query(q.id.clone()).await?;
            }
            "subscription_menu" => {
                let user_details_opt = db_pool.get_user(user_id).await.ok().flatten();
                let (level, end_date_str) = if let Some(details) = user_details_opt {
                    let sub_end = details.subscription_end.map(|dt| dt.with_timezone(&*CONFIG.msk_tz).format("%d.%m.%Y").to_string());
                    (details.subscription_level, sub_end)
                } else { (0, None) };

                let text = format!("👑 <b>Управление подпиской</b>\n\nВаш текущий уровень: {}.",
                    match level { 0 => "Free", 1 => "Standard", 2 => "Premium", 3 => "Max", _ => "Неизвестный" });
                let keyboard = keyboards::create_subscription_menu_keyboard(level, end_date_str);
                bot.edit_message_text(original_message.chat.id, original_message.id, text)
                    .reply_markup(keyboard)
                    .parse_mode(ParseMode::Html)
                    .await?;
                bot.answer_callback_query(q.id.clone()).await?;
            }
            "help_info" => {
                let help_text = format!(
                    "ℹ️ <b>Помощь и информация</b>\n\nБот предоставляет доступ к различным AI моделям...\n\nПо вопросам подписки: @{}\nТехническая поддержка: @{}",
                    CONFIG.sub_contact, CONFIG.support_contact); // Shortened for brevity
                let keyboard = InlineKeyboardMarkup::new(vec![vec![InlineKeyboardButton::callback("🔙 Назад в главное меню", "main_menu")]]);
                bot.edit_message_text(original_message.chat.id, original_message.id, help_text)
                    .reply_markup(keyboard).parse_mode(ParseMode::Html).await?;
                bot.answer_callback_query(q.id.clone()).await?;
            }
            _ if data.starts_with("set_text_model:") => {
                let model_name = data.trim_start_matches("set_text_model:");
                match db_pool.set_last_used_model(user_id, model_name).await {
                    Ok(_) => {
                        let keyboard = keyboards::create_text_model_selection_keyboard(Some(model_name));
                        bot.edit_message_reply_markup(original_message.chat.id, original_message.id)
                            .reply_markup(keyboard).await?;
                        bot.answer_callback_query(q.id.clone()).text(&format!("Текстовая модель изменена на {}", model_name)).await?;
                    }
                    Err(e) => {
                        log::error!("Failed to set text model for user {}: {}", user_id, e);
                        bot.answer_callback_query(q.id.clone()).text("Ошибка при смене модели.").show_alert(true).await?;
                    }
                }
            }
             _ if data.starts_with("set_image_model:") => {
                let model_name = data.trim_start_matches("set_image_model:");
                match db_pool.set_last_used_image_model(user_id, model_name).await {
                    Ok(_) => {
                        let keyboard = keyboards::create_image_generation_menu_keyboard(Some(model_name));
                        bot.edit_message_reply_markup(original_message.chat.id, original_message.id)
                            .reply_markup(keyboard).await?;
                        bot.answer_callback_query(q.id.clone()).text(&format!("Модель изображений изменена на {}", model_name)).await?;
                    }
                    Err(e) => {
                        log::error!("Failed to set image model for user {}: {}", user_id, e);
                        bot.answer_callback_query(q.id.clone()).text("Ошибка при смене модели изображений.").show_alert(true).await?;
                    }
                }
            }
            "admin_panel" => {
                if CONFIG.admin_ids.contains(&user_id) {
                    let text = "🛠️ <b>Админ-панель</b>";
                    let keyboard = keyboards::create_admin_panel_keyboard();
                    bot.edit_message_text(original_message.chat.id, original_message.id, text)
                        .reply_markup(keyboard).parse_mode(ParseMode::Html).await?;
                    bot.answer_callback_query(q.id.clone()).await?;
                } else {
                    bot.answer_callback_query(q.id.clone()).text("⛔ Доступ запрещен.").show_alert(true).await?;
                }
            }
            "sub_status_info" => { bot.answer_callback_query(q.id.clone()).await?; }
            _ => {
                log::warn!("Unhandled callback data: '{}' from user {}", data, user_id);
                bot.answer_callback_query(q.id.clone()).text("Действие не распознано или в разработке.").await?;
            }
        }
    } else {
        log::warn!("Callback query from user {} has no data.", user_id);
        bot.answer_callback_query(q.id.clone()).text("Ошибка: нет данных для обработки.").show_alert(true).await?;
    }

    Ok(())
}
