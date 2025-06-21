// src/handlers/common_handlers.rs

use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::utils::command::BotCommands;
use teloxide::types::{Message, ParseMode};
use teloxide::payloads::SendMessageSetters; // For .message_thread_id() if needed

use crate::db;
use crate::Command;
use crate::keyboards;
use crate::states::{State, MyDialogue}; // Import MyDialogue and State
use crate::user_service::{self, AppCache}; // For captcha and user verification

// Renamed to reflect it's part of the dialogue system
pub async fn handle_commands_dialogue(
    bot: Bot,
    dialogue: MyDialogue,
    msg: Message,
    cmd: Command,
    db_pool: Arc<db::Database>,
    bot: Bot,
    dialogue: MyDialogue,
    msg: Message,
    cmd: Command,
    db_pool: Arc<db::Database>,
    app_cache: Arc<tokio::sync::Mutex<AppCache>>,
    // http_client: Arc<reqwest::Client> // Will be needed if commands directly interact with AI
) -> anyhow::Result<()> {
    match cmd {
        Command::Help => {
            bot.send_message(msg.chat.id, Command::descriptions().to_string()).await?;
            // dialogue.exit().await?; // Help usually doesn't need to affect current dialogue state
        }
        Command::Start | Command::Menu => {
            // This will also effectively stop any active chat if user types /menu or /start
            let user_telegram = msg.from().cloned();
            if user_telegram.is_none() {
                log::warn!("Received /start or /menu from a message with no user info.");
                return Ok(());
            }
            let user_tg = user_telegram.unwrap();
            let user_id = user_tg.id.0 as i64;
            let username = user_tg.username.clone();

            log::info!("Processing /start or /menu for user_id: {} ({:?})", user_id, username.as_deref().unwrap_or("N/A"));

            let mut cache_guard = app_cache.lock().await;
            match db_pool.add_user(user_id, username.as_deref()).await {
                Ok(is_new_db_user) => {
                    log::info!("User {} DB entry ensured. New DB user: {}", user_id, is_new_db_user);

                    // Check verification status
                    let is_verified = user_service::is_user_verified_in_db(user_id, &db_pool, &mut cache_guard).await
                        .unwrap_or_else(|e| {
                            log::error!("DB error checking user verification for {}: {}", user_id, e);
                            false // Assume not verified on DB error to be safe
                        });

                    if !is_verified {
                        log::info!("User {} is not verified. Sending captcha.", user_id);
                        match user_service::prepare_captcha_data().await {
                            Ok((captcha_text, expected_answer, _variant)) => {
                                let sent_captcha_msg = bot.send_message(msg.chat.id, captcha_text)
                                    .parse_mode(ParseMode::Html)
                                    .await?;
                                dialogue.update(State::WaitingCaptchaAnswer {
                                    expected_answer,
                                    original_message_id_to_delete: Some(sent_captcha_msg.id.0)
                                }).await?;
                            }
                            Err(e) => {
                                log::error!("Failed to prepare captcha: {}", e);
                                bot.send_message(msg.chat.id, "Не удалось подготовить капчу. Попробуйте позже.").await?;
                                dialogue.exit().await?;
                            }
                        }
                    } else {
                        log::info!("User {} is verified. Sending main menu.", user_id);
                        let menu_text = "🤖 <b>Главное меню</b>\n\nВыберите действие:";
                        let keyboard = keyboards::create_main_menu_keyboard(user_id, &db_pool).await;
                        bot.send_message(msg.chat.id, menu_text)
                            .reply_markup(keyboard)
                            .parse_mode(ParseMode::Html)
                            .await?;
                        dialogue.update(State::MainMenu).await?;
                    }
                }
                Err(e) => {
                    log::error!("Failed to add/update user {} ({:?}): {}", user_id, username.as_deref().unwrap_or("N/A"), e);
                    bot.send_message(msg.chat.id, "Произошла ошибка при взаимодействии с базой данных. Попробуйте позже.").await?;
                    dialogue.exit().await?;
                }
            }
        }
        Command::StopChat => {
            if let Some(State::ActiveChat {..}) = dialogue.state().await? {
                let user_id = msg.from().map_or(0, |u| u.id.0 as i64); // Should always exist for commands
                log::info!("User {} stopped chat session via /stopchat.", user_id);
                // Send main menu
                let menu_text = "🤖 <b>Главное меню</b>\n\nЧат завершен. Выберите действие:";
                let keyboard = keyboards::create_main_menu_keyboard(user_id, &db_pool).await;
                bot.send_message(msg.chat.id, menu_text)
                    .reply_markup(keyboard)
                    .parse_mode(ParseMode::Html)
                    .await?;
                dialogue.update(State::MainMenu).await?;
            } else {
                bot.send_message(msg.chat.id, "Нет активного чата для завершения.").await?;
            }
        }
    }
    Ok(())
}

// Handles messages based on the current dialogue state
pub async fn handle_dialogue_messages(
    bot: Bot,
    dialogue: MyDialogue,
    msg: Message, // The new message from the user
    db_pool: Arc<db::Database>,
    app_cache: Arc<tokio::sync::Mutex<AppCache>>,
    http_client: Arc<reqwest::Client>, // For AI service
    // config: Arc<crate::config::AppConfig> // For API keys, URLs if not using global CONFIG
) -> anyhow::Result<()> {
    // Ensure there's text in the message
    let current_text = match msg.text() {
        Some(text) => text,
        None => {
            if let Some(State::ActiveChat {..}) | Some(State::WaitingCaptchaAnswer {..}) | Some(State::WaitingUserSettingsInstruction {..}) | Some(State::WaitingUserSettingsTemperature {..}) | Some(State::WaitingImagePrompt {..})  = dialogue.state().await? {
                 bot.send_message(msg.chat.id, "Пожалуйста, введите текстовый ответ.").await?;
            }
            return Ok(());
        }
    };

    let user_id = msg.from().map_or(0, |u| u.id.0 as i64); // Should always exist for messages from users

    match dialogue.state().await? {
        Some(State::WaitingCaptchaAnswer { expected_answer, original_message_id_to_delete }) => {
            // let user_id = msg.from().map_or(0, |u| u.id.0 as i64); // already got user_id
            if current_text.trim().to_lowercase() == expected_answer.to_lowercase() {
                log::info!("User {} solved captcha correctly.", user_id);
                match db_pool.set_user_verified(user_id, true).await {
                    Ok(_) => {
                        // Invalidate cache for this user as their status changed
                        let mut cache_guard = app_cache.lock().await;
                        user_service::invalidate_user_cache(user_id, &mut cache_guard);
                        drop(cache_guard); // Release lock

                        bot.send_message(msg.chat.id, "✅ Капча пройдена! Доступ разрешен.").await?;

                        // Delete the original captcha prompt message if ID is known
                        if let Some(message_id_val) = original_message_id_to_delete {
                             bot.delete_message(msg.chat.id, MessageId(message_id_val)).await.unwrap_or_else(|e| {
                                log::warn!("Failed to delete captcha original message {}: {}", message_id_val, e);
                                /* Default teloxide::RequestError does not implement Error */
                                teloxide::requests::ResponseResult::Ok(())
                            });
                        }
                        // Delete the user's answer message
                        bot.delete_message(msg.chat.id, msg.id).await.unwrap_or_else(|e| {
                            log::warn!("Failed to delete captcha answer message {}: {}", msg.id, e);
                            teloxide::requests::ResponseResult::Ok(())
                        });


                        // Send main menu
                        let menu_text = "🤖 <b>Главное меню</b>\n\nВыберите действие:";
                        let keyboard = keyboards::create_main_menu_keyboard(user_id, &db_pool).await;
                        bot.send_message(msg.chat.id, menu_text)
                            .reply_markup(keyboard)
                            .parse_mode(ParseMode::Html)
                            .await?;
                        dialogue.update(State::MainMenu).await?;
                    }
                    Err(e) => {
                        log::error!("Failed to set user {} as verified: {}", user_id, e);
                        bot.send_message(msg.chat.id, "Ошибка при обновлении вашего статуса. Попробуйте /start снова.").await?;
                        dialogue.exit().await?;
                    }
                }
            } else {
                log::info!("User {} failed captcha. Expected '{}', got '{}'", user_id, expected_answer, current_text);
                // Re-send captcha or inform of failure
                match user_service::prepare_captcha_data().await {
                    Ok((new_captcha_text, new_expected_answer, _)) => {
                        bot.send_message(msg.chat.id, format!("Неверный ответ. Попробуйте еще раз:\n\n{}",new_captcha_text))
                            .parse_mode(ParseMode::Html).await?;
                        dialogue.update(State::WaitingCaptchaAnswer{
                            expected_answer: new_expected_answer,
                            original_message_id_to_delete, // Keep original prompt ID or update if new one sent
                        }).await?;
                    }
                    Err(e) => {
                         log::error!("Failed to re-prepare captcha: {}", e);
                        bot.send_message(msg.chat.id, "Ошибка при подготовке новой капчи. Попробуйте /start.").await?;
                        dialogue.exit().await?;
                    }
                }
            }
        }
        Some(State::MainMenu) | Some(State::Start) | None => {
            // If user sends random text when in main menu or no specific dialogue
            // This could be where general chat functionality starts, or just a help message.
            // For now, let's assume it means they want to chat.
            // TODO: Implement actual chat logic (State::ActiveChat)
            log::debug!("User {} sent text '{}' in MainMenu/Start/None state. Forwarding to chat (placeholder).", user_id, current_text); // Used user_id
             bot.send_message(msg.chat.id, format!("Вы сказали: {}. Чат-функционал в разработке. Используйте кнопки меню.", current_text)).await?;
             // dialogue.update(State::ActiveChat { history: vec![], current_model: "default".to_string() }).await?;
        }
        Some(State::ActiveChat { mut history, current_model }) => {
            log::info!("User {} in ActiveChat with model {}: {}", user_id, current_model, current_text);

            // Add user's message to history
            history.push(("user".to_string(), current_text.to_string()));

            // Prepare messages for AI service
            // The Python version prepends system prompts in ai_service.get_simple_response
            // So we just send the current history as is.
            let ai_messages: Vec<std::collections::HashMap<String, String>> = history.iter()
                .map(|(role, content)| {
                    let mut map = std::collections::HashMap::new();
                    map.insert("role".to_string(), role.clone());
                    map.insert("content".to_string(), content.clone());
                    map
                })
                .collect();

            // Send "typing..." action
            bot.send_chat_action(msg.chat.id, teloxide::types::ChatAction::Typing).await?;

            match crate::ai_service::get_simple_response(
                &http_client,
                &crate::config::CONFIG.api_key,
                &crate::config::CONFIG.api_url,
                &current_model,
                ai_messages,
                user_id,
                &db_pool,
                // &mut cache_guard, // get_simple_response needs mutable cache if it uses get_user_details_cached_rust
            ).await {
                Ok((ai_response_text, _duration)) => {
                    history.push(("assistant".to_string(), ai_response_text.clone()));
                    // Update dialogue state with new history
                    dialogue.update(State::ActiveChat { history, current_model }).await?;
                    bot.send_message(msg.chat.id, ai_response_text).await?;
                }
                Err(e) => {
                    log::error!("AI service failed for user {} in ActiveChat: {}", user_id, e);
                    bot.send_message(msg.chat.id, format!("Произошла ошибка при обращении к AI: {}. Попробуйте еще раз или /stopchat для выхода.", e)).await?;
                    // Optionally, remove last user message from history if AI failed, or keep it.
                    // For now, keeping it. The user can try again.
                }
            }
        }
        Some(State::WaitingUserSettingsInstruction { original_message_id_to_delete }) => {
            // let user_id = msg.from().map_or(0, |u| u.id.0 as i64); // already got user_id
            let instruction_text = current_text.trim();

            if instruction_text.is_empty() || instruction_text.to_lowercase() == "удалить" || instruction_text.to_lowercase() == "сбросить" {
                 match db_pool.set_user_instruction(user_id, None).await {
                    Ok(_) => {
                        bot.send_message(msg.chat.id, "✅ Ваша системная инструкция была удалена.").await?;
                        log::info!("User {} cleared their system instruction.", user_id);
                    }
                    Err(e) => {
                        log::error!("Failed to clear user instruction for {}: {}", user_id, e);
                        bot.send_message(msg.chat.id, "⚠️ Не удалось удалить инструкцию. Попробуйте позже.").await?;
                    }
                }
            } else {
                match db_pool.set_user_instruction(user_id, Some(instruction_text)).await {
                    Ok(_) => {
                        bot.send_message(msg.chat.id, "✅ Ваша системная инструкция сохранена!").await?;
                        log::info!("User {} set system instruction to: {}", user_id, instruction_text);
                    }
                    Err(e) => {
                        log::error!("Failed to set user instruction for {}: {}", user_id, e);
                        bot.send_message(msg.chat.id, "⚠️ Не удалось сохранить инструкцию. Попробуйте позже.").await?;
                    }
                }
            }

            // Clean up messages
            if let Some(prompt_msg_id) = original_message_id_to_delete {
                bot.delete_message(msg.chat.id, MessageId(prompt_msg_id)).await.unwrap_or_else(|e| {
                    log::warn!("Failed to delete instruction prompt message {}: {}", prompt_msg_id, e);
                    teloxide::requests::ResponseResult::Ok(())
                });
            }
            bot.delete_message(msg.chat.id, msg.id).await.unwrap_or_else(|e| {
                log::warn!("Failed to delete user instruction input message {}: {}", msg.id, e);
                teloxide::requests::ResponseResult::Ok(())
            });

            // Return to settings menu
            let settings_text = "⚙️ <b>Настройки</b>\n\nЗдесь вы можете настроить бота под себя.";
            let settings_keyboard = keyboards::create_settings_keyboard();
            bot.send_message(msg.chat.id, settings_text)
                .reply_markup(settings_keyboard)
                .parse_mode(ParseMode::Html)
                .await?;
            dialogue.update(State::SettingsMenu).await?;
        }
        Some(State::WaitingUserSettingsTemperature { original_message_id_to_delete }) => {
            let user_id = msg.from().map_or(0, |u| u.id.0 as i64);
            let temp_text = current_text.trim();

            if temp_text.to_lowercase() == "сбросить" || temp_text.to_lowercase() == "удалить" {
                match db_pool.set_user_temperature(user_id, None).await {
                    Ok(_) => {
                        bot.send_message(msg.chat.id, "✅ Температура сброшена к значению по умолчанию.").await?;
                        log::info!("User {} reset their temperature setting.", user_id);
                    }
                    Err(e) => {
                        log::error!("Failed to reset user temperature for {}: {}", user_id, e);
                        bot.send_message(msg.chat.id, "⚠️ Не удалось сбросить температуру. Попробуйте позже.").await?;
                    }
                }
            } else {
                match temp_text.parse::<f64>() {
                    Ok(temp_val) if (0.0..=2.0).contains(&temp_val) => {
                        match db_pool.set_user_temperature(user_id, Some(temp_val)).await {
                            Ok(_) => {
                                bot.send_message(msg.chat.id, format!("✅ Температура установлена на: {:.1}", temp_val)).await?;
                                log::info!("User {} set temperature to: {:.1}", user_id, temp_val);
                            }
                            Err(e) => {
                                log::error!("Failed to set user temperature for {}: {}", user_id, e);
                                bot.send_message(msg.chat.id, "⚠️ Не удалось сохранить температуру. Попробуйте позже.").await?;
                            }
                        }
                    }
                    _ => { // Parsing failed or value out of range
                        bot.send_message(msg.chat.id, "⚠️ Неверный формат. Введите число от 0.0 до 2.0 (например, 0.7) или 'сбросить'.").await?;
                        // Keep the user in the same state to allow them to try again
                        // We don't delete messages here so they can see their mistake.
                        // Or, resend prompt:
                        // let prompt_msg = bot.send_message(msg.chat.id, "Введите температуру (0.0-2.0) или 'сбросить':").await?;
                        // dialogue.update(State::WaitingUserSettingsTemperature { original_message_id_to_delete: Some(prompt_msg.id.0) }).await?;
                        return Ok(()); // Return early, don't proceed to cleanup/menu
                    }
                }
            }

            // Clean up messages (only if input was valid or reset)
            if let Some(prompt_msg_id) = original_message_id_to_delete {
                 bot.delete_message(msg.chat.id, MessageId(prompt_msg_id)).await.unwrap_or_else(|e| {
                    log::warn!("Failed to delete temperature prompt message {}: {}", prompt_msg_id, e);
                    teloxide::requests::ResponseResult::Ok(())
                });
            }
            bot.delete_message(msg.chat.id, msg.id).await.unwrap_or_else(|e| {
                log::warn!("Failed to delete user temperature input message {}: {}", msg.id, e);
                teloxide::requests::ResponseResult::Ok(())
            });

            // Return to settings menu
            let settings_text = "⚙️ <b>Настройки</b>\n\nЗдесь вы можете настроить бота под себя.";
            let settings_keyboard = keyboards::create_settings_keyboard();
            bot.send_message(msg.chat.id, settings_text)
                .reply_markup(settings_keyboard)
                .parse_mode(ParseMode::Html)
                .await?;
            dialogue.update(State::SettingsMenu).await?;
        }
        // TODO: Add handlers for other states like ActiveChat, etc.
        _ => { // Default for any other state not explicitly handled yet
            log::debug!("Message received in unhandled dialogue state for chat {}: {:?}", msg.chat.id, dialogue.state().await?);
        }
    }
    Ok(())
}
