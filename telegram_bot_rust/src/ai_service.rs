use crate::config::{AppConfig, CONFIG}; // Assuming CONFIG is the global AppConfig instance
use crate::db::{Database, User}; // Assuming User struct contains all details after get_user_details_cached
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::Instant;
use log::{debug, info, warn, error};
use tokio::time::Duration as TokioDuration; // Alias to avoid conflict with chrono::Duration

// --- Structs for API interaction (matching OpenAI library) ---
#[derive(Serialize, Debug, Clone)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Serialize, Debug)]
struct ChatCompletionRequest {
    model: String,
    messages: Vec<ChatMessage>,
    temperature: Option<f64>,
    // timeout is handled by reqwest client, not part of OpenAI payload
}

#[derive(Deserialize, Debug)]
struct ChatCompletionChoice {
    message: Option<ChatMessageContent>, // Made Option to handle null message
    finish_reason: Option<String>,
}

#[derive(Deserialize, Debug)]
struct ChatMessageContent { // Renamed from ChatMessage to avoid conflict
    role: Option<String>, // Role might not always be present in response message
    content: Option<String>, // Content can be null
}

#[derive(Deserialize, Debug)]
struct ChatCompletionResponse {
    choices: Vec<ChatCompletionChoice>,
    // Add other fields if needed, like 'usage'
}

// --- Structs for Image Generation API ---
#[derive(Serialize, Debug)]
struct ImageGenerationRequest {
    model: String,
    prompt: String,
    height: Option<i32>,
    width: Option<i32>,
    response_format: Option<String>, // "url" or "b64_json"
                                     // Add other params like n, quality, style if needed
}

#[derive(Deserialize, Debug)]
struct ImageUrl {
    url: String,
    // revised_prompt: Option<String> // if API returns it
}

#[derive(Deserialize, Debug)]
struct ImageB64 {
    b64_json: String,
    // revised_prompt: Option<String> // if API returns it
}


#[derive(Deserialize, Debug)]
#[serde(untagged)] // Allows deserializing into either ImageUrl or ImageB64 based on fields
enum ImageData {
    Url(ImageUrl),
    B64(ImageB64),
}

#[derive(Deserialize, Debug)]
struct ImageGenerationResponse {
    created: Option<i64>, // timestamp
    data: Vec<ImageData>,
}


// --- Service Functions ---

/// Fetches user details. In a real scenario, this would involve caching as in Python.
/// For now, it directly queries the DB. The Python version uses a `cache` dictionary.
/// We'll need a proper caching mechanism in Rust (e.g. `cached` crate on the function later).
async fn get_user_details_cached_rust(
    user_id: i64,
    db: &Database,
    // cache: &Cache // Placeholder for a proper cache implementation
) -> Result<Option<User>, sqlx::Error> {
    // TODO: Implement actual caching similar to Python's TTLCache
    db.get_user(user_id).await
}

pub async fn get_simple_response(
    http_client: &Client, // reqwest client
    ai_api_key: &str,
    ai_api_url: &str, // Base URL like "https://nustjourney.mirandasite.online/v1"
    model: &str,
    messages: Vec<HashMap<String, String>>, // Python's [{"role": "user", "content": "..."}]
    user_id: i64,
    db: &Database,
    // cache: &Cache // Placeholder for cache
) -> Result<(String, f32), String> {
    let start_time = Instant::now();

    let user_details = match get_user_details_cached_rust(user_id, db).await {
        Ok(Some(ud)) => ud,
        Ok(None) => return Err(format!("User {} not found", user_id)),
        Err(e) => return Err(format!("Failed to get user details for {}: {}", user_id, e)),
    };

    let user_instruction = user_details.user_instruction;
    let user_temperature = user_details.user_temperature.or(Some(CONFIG.default_temperature));

    let mut final_messages: Vec<ChatMessage> = Vec::new();
    final_messages.push(ChatMessage {
        role: "system".to_string(),
        content: CONFIG.global_system_prompt.clone(),
    });
    if let Some(instruction) = user_instruction {
        final_messages.push(ChatMessage {
            role: "system".to_string(),
            content: format!("Дополнительная инструкция от пользователя: {}", instruction),
        });
    }
    for msg_map in messages {
        final_messages.push(ChatMessage {
            role: msg_map.get("role").cloned().unwrap_or_default(),
            content: msg_map.get("content").cloned().unwrap_or_default(),
        });
    }

    let request_payload = ChatCompletionRequest {
        model: model.to_string(),
        messages: final_messages,
        temperature: user_temperature,
    };

    debug!("Requesting model {} for user {}. Payload: {:?}", model, user_id, request_payload);

    let request_url = format!("{}/chat/completions", ai_api_url.trim_end_matches('/'));

    match http_client
        .post(&request_url)
        .bearer_auth(ai_api_key)
        .json(&request_payload)
        .timeout(TokioDuration::from_secs(120))
        .send()
        .await
    {
        Ok(response) => {
            let duration_secs = start_time.elapsed().as_secs_f32();
            if response.status().is_success() {
                match response.json::<ChatCompletionResponse>().await {
                    Ok(chat_response) => {
                        if let Some(choice) = chat_response.choices.get(0) {
                            if let Some(msg_content) = &choice.message {
                                if let Some(text) = &msg_content.content {
                                    debug!("Model {} for user {} responded in {:.2f}s", model, user_id, duration_secs);
                                    return Ok((text.clone(), duration_secs));
                                }
                            }
                            warn!(
                                "Model {} for user {} returned a response with no content. Finish reason: {:?}",
                                model, user_id, choice.finish_reason
                            );
                            Ok(("".to_string(), duration_secs)) // Return empty string as per Python logic
                        } else {
                            warn!("Model {} for user {} returned no choices.", model, user_id);
                            Ok(("".to_string(), duration_secs))
                        }
                    }
                    Err(e) => {
                        error!("Failed to parse JSON response from model {} for user {}. Error: {}", model, user_id, e);
                        Err(format!("JSON parsing error: {}", e))
                    }
                }
            } else {
                let status = response.status();
                let error_text = response.text().await.unwrap_or_else(|_| "Unknown error".to_string());
                error!(
                    "API request failed for model {} user {}. Status: {}. Body: {}",
                    model, user_id, status, error_text
                );
                Err(format!("API error {}: {}", status, error_text))
            }
        }
        Err(e) => {
            error!("Failed to send request to model {} for user {}. Error: {}", model, user_id, e);
            Err(format!("Request error: {}", e))
        }
    }
}

async fn get_participant_response_internal(
    http_client: &Client,
    ai_api_key: &str,
    ai_api_url: &str,
    model_name: String, // Owned String to move into async block
    prompt: String,     // Owned String
    user_id: i64,
    db: &Database,
    // cache: &Cache
) -> (String, String) {
    let messages = vec![HashMap::from([
        ("role".to_string(), "user".to_string()),
        ("content".to_string(), prompt),
    ])];
    match get_simple_response(http_client, ai_api_key, ai_api_url, &model_name, messages, user_id, db /*, cache*/).await {
        Ok((response, _duration)) => (model_name, response),
        Err(e) => {
            warn!("Max Mode participant {} failed for user {}. Error: {}", model_name, user_id, e);
            (model_name, format!("ОШИБКА: Модель не смогла обработать запрос. ({})", e))
        }
    }
}


pub async fn get_max_mode_response(
    http_client: &Client,
    ai_api_key: &str,
    ai_api_url: &str,
    prompt: &str,
    user_id: i64,
    db: &Database,
    // cache: &Cache
) -> Result<(String, f32), String> {
    let full_start_time = Instant::now();
    info!("Starting Max Mode for user {}", user_id);

    let mut tasks = Vec::new();
    for model_name in &CONFIG.max_mode_participants {
        // Clone necessary data for each concurrent task
        let client_clone = http_client.clone();
        let key_clone = ai_api_key.to_string();
        let url_clone = ai_api_url.to_string();
        let model_name_clone = model_name.clone();
        let prompt_clone = prompt.to_string();
        let db_clone = db.clone(); // Assuming Database is Cloneable (needs SqlitePool to be Arc-wrapped or Database itself Arc-wrapped)
        // let cache_clone = cache.clone();

        tasks.push(tokio::spawn(get_participant_response_internal(
            &client_clone, // This needs to be a reference if client is not Clone or cheap to clone.
                           // If Client is Arc<InnerClient>, then clone the Arc.
                           // Reqwest Client is Arc-based internally, so cloning is cheap.
            &key_clone,
            &url_clone,
            model_name_clone,
            prompt_clone,
            user_id,
            &db_clone, // Pass by reference if Database is Clone
            // &cache_clone,
        )));
    }

    let participant_results_futures = futures::future::join_all(tasks).await;
    let mut participant_results = Vec::new();
    for res in participant_results_futures {
        match res {
            Ok(data) => participant_results.push(data),
            Err(e) => {
                error!("Tokio spawn error in max_mode: {}", e);
                // Decide how to handle a panicked task, maybe add a placeholder error
                 participant_results.push(("PANICKED_TASK".to_string(), format!("ОШИБКА: Задача для модели завершилась аварийно. ({})",e)));
            }
        }
    }

    info!("Max Mode participant results for user {}: {:?}", user_id, participant_results);

    let mut meta_prompt_parts: Vec<String> = vec![
        "Ты — главный AI-арбитр. Твоя задача — проанализировать ответы от нескольких моделей и создать один, наилучший итоговый ответ.".to_string(),
        "Действуй строго по шагам:".to_string(),
        "\n**ШАГ 1: Определи правильный ответ.**".to_string(),
        "Внимательно изучи оригинальный запрос пользователя и все предоставленные ответы. Вычисли или определи единственно верный и точный ответ.".to_string(),
        "\n**ШАГ 2: Сформируй финальный ответ.**".to_string(),
        "Напиши исчерпывающий, точный и хорошо отформанированный ответ для пользователя. Используй лучшие идеи и факты из ответов-участников, но изложи их своими словами. Не упоминай другие модели в этой части.".to_string(),
        "\n**ШАГ 3: Проведи анализ источников.**".to_string(),
        "После финального ответа поставь разделитель `---`. Затем кратко и объективно проанализируй ответы участников. Укажи, кто был прав, кто ошибся и почему. Твой анализ должен быть полностью консистентен с финальным ответом, который ты дал на ШАГЕ 2.".to_string(),
        format!("\n---"),
        format!("**ОРИГИНАЛЬНЫЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ:**\n{}\n", prompt),
        "---".to_string(),
        "\n**ОТВЕТЫ МОДЕЛЕЙ-УЧАСТНИКОВ ДЛЯ АНАЛИЗА:**".to_string(),
    ];

    let mut successful_responses = 0;
    for (model_name, response_text) in &participant_results {
        let safe_response_text = if response_text.is_empty() { "ОШИБКА: Модель не вернула текстовый ответ." } else { response_text };
        // In Rust, hcode equivalent would be `format!("`{}`", model_name)` for Markdown
        meta_prompt_parts.push(format!("\n**Ответ от модели (`{}`):**\n{}\n---", model_name, safe_response_text));
        if !safe_response_text.starts_with("ОШИБКА:") {
            successful_responses += 1;
        }
    }

    if successful_responses == 0 {
        error!("Max Mode failed for user {}: all participants returned an error or empty content.", user_id);
        return Err("К сожалению, все модели-участники не смогли дать ответ. Попробуйте позже.".to_string());
    }

    meta_prompt_parts.push("\n**ТВОЙ ИТОГОВЫЙ РЕЗУЛЬТАТ (выполни ШАГ 2 и ШАГ 3):**".to_string());
    let meta_prompt = meta_prompt_parts.join("\n");

    info!("Sending meta-prompt to arbiter {} for user {}", &CONFIG.max_mode_arbiter, user_id);

    let arbiter_messages = vec![HashMap::from([
        ("role".to_string(), "user".to_string()),
        ("content".to_string(), meta_prompt),
    ])];

    match get_simple_response(
        http_client, ai_api_key, ai_api_url,
        &CONFIG.max_mode_arbiter, arbiter_messages, user_id, db /*, cache*/
    ).await {
        Ok((final_response_text, _)) => {
            let total_duration_secs = full_start_time.elapsed().as_secs_f32();
            info!("Max Mode for user {} finished in {:.2f}s", user_id, total_duration_secs);
            Ok((final_response_text, total_duration_secs))
        }
        Err(e) => {
            error!("Max Mode arbiter {} failed for user {}. Error: {}", &CONFIG.max_mode_arbiter, user_id, e);
            Err(format!("Модель-арбитр ({}) не смогла обработать ответы. Попробуйте позже. ({})", &CONFIG.max_mode_arbiter, e))
        }
    }
}


// --- Image Generation Service ---
pub async fn generate_image(
    http_client: &Client,
    ai_api_key: &str,
    ai_api_url: &str, // Base URL like "https://nustjourney.mirandasite.online/v1"
    model: &str,
    prompt: &str,
    height: Option<i32>,
    width: Option<i32>,
    response_format: Option<&str>, // "url" or "b64_json"
    user_id: i64, // For logging
) -> Result<ImageGenerationResponse, String> {
    let start_time = Instant::now();

    let request_payload = ImageGenerationRequest {
        model: model.to_string(),
        prompt: prompt.to_string(),
        height,
        width,
        response_format: response_format.map(String::from),
    };

    debug!("Requesting image generation model {} for user {}. Payload: {:?}", model, user_id, request_payload);

    let request_url = format!("{}/images/generations", ai_api_url.trim_end_matches('/'));

    match http_client
        .post(&request_url)
        .bearer_auth(ai_api_key)
        .json(&request_payload)
        .timeout(TokioDuration::from_secs(180)) // Longer timeout for images
        .send()
        .await
    {
        Ok(response) => {
            let duration_secs = start_time.elapsed().as_secs_f32();
            if response.status().is_success() {
                match response.json::<ImageGenerationResponse>().await {
                    Ok(image_response) => {
                        debug!("Image model {} for user {} responded in {:.2f}s", model, user_id, duration_secs);
                        Ok(image_response)
                    }
                    Err(e) => {
                        let body_text = response.text().await.unwrap_or_else(|_| "Failed to read error body".to_string());
                        error!("Failed to parse JSON response from image model {} for user {}. Error: {}, Body: {}", model, user_id, e, body_text);
                        Err(format!("Image API JSON parsing error: {}", e))
                    }
                }
            } else {
                let status = response.status();
                let error_text = response.text().await.unwrap_or_else(|_| "Unknown error".to_string());
                error!(
                    "Image API request failed for model {} user {}. Status: {}. Body: {}",
                    model, user_id, status, error_text
                );
                Err(format!("Image API error {}: {}", status, error_text))
            }
        }
        Err(e) => {
            error!("Failed to send request to image model {} for user {}. Error: {}", model, user_id, e);
            Err(format!("Image API request error: {}", e))
        }
    }
}
