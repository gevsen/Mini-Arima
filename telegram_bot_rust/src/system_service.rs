use crate::config::{AppConfig, CONFIG};
use crate::db::Database;
use crate::ai_service::{ChatMessage, ChatCompletionRequest, ImageGenerationRequest}; // For request structs
use crate::user_service::Cache as AppCache; // Using the cache defined in user_service

use chrono::{DateTime, Utc, Duration as ChronoDuration};
use reqwest::Client as HttpClient; // Renamed to avoid conflict
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use std::collections::HashSet;
use std::sync::Arc; // For sharing db and http_client across tasks
use log::{debug, info, warn, error};
use tokio::time::Duration as TokioDuration;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct ModelStatusInfo {
    pub model: String,
    pub status: String, // "OK", "Timeout", "API Error XXX", "Error: Type"
}

// --- Model Test Functions ---

pub async fn test_chat_model(
    http_client: Arc<HttpClient>,
    ai_api_key: Arc<String>,
    ai_api_url: Arc<String>,
    model: String, // Take ownership
) -> ModelStatusInfo {
    let request_payload = ChatCompletionRequest {
        model: model.clone(),
        messages: vec![ChatMessage {
            role: "user".to_string(),
            content: "Test".to_string(),
        }],
        temperature: Some(0.7),
        // max_tokens: Some(10), // Assuming ChatCompletionRequest has this field if needed
    };
    let request_url = format!("{}/chat/completions", ai_api_url.trim_end_matches('/'));

    debug!("Testing chat model: {}", model);
    match http_client
        .post(&request_url)
        .bearer_auth(&*ai_api_key)
        .json(&request_payload)
        .timeout(TokioDuration::from_secs(20))
        .send()
        .await
    {
        Ok(response) => {
            if response.status().is_success() {
                // We don't need to parse the body for a simple health check if status is OK
                ModelStatusInfo { model, status: "OK".to_string() }
            } else {
                let status_code = response.status().as_u16();
                warn!("Chat model {} test failed with APIError: {}", model, status_code);
                ModelStatusInfo { model, status: format!("API Error {}", status_code) }
            }
        }
        Err(e) => {
            if e.is_timeout() {
                warn!("Chat model {} test timed out.", model);
                ModelStatusInfo { model, status: "Timeout".to_string() }
            } else {
                error!("Chat model {} test failed with unexpected error: {}", model, e);
                ModelStatusInfo { model, status: format!("Error: {}", e) } // Simplified error type
            }
        }
    }
}

pub async fn test_image_model(
    http_client: Arc<HttpClient>,
    ai_api_key: Arc<String>,
    ai_api_url: Arc<String>,
    model: String, // Take ownership
) -> ModelStatusInfo {
    let request_payload = ImageGenerationRequest {
        model: model.clone(),
        prompt: "Test".to_string(),
        height: Some(512),
        width: Some(512),
        response_format: Some("url".to_string()),
        // n: Some(1) // Assuming ImageGenerationRequest has this field if needed
    };
    let request_url = format!("{}/images/generations", ai_api_url.trim_end_matches('/'));
    debug!("Testing image model: {}", model);

    match http_client
        .post(&request_url)
        .bearer_auth(&*ai_api_key)
        .json(&request_payload)
        .timeout(TokioDuration::from_secs(45))
        .send()
        .await
    {
        Ok(response) => {
            if response.status().is_success() {
                ModelStatusInfo { model, status: "OK".to_string() }
            } else {
                let status_code = response.status().as_u16();
                warn!("Image model {} test failed with status {}", model, status_code);
                ModelStatusInfo { model, status: format!("Error {}", status_code) }
            }
        }
        Err(e) => {
            if e.is_timeout() {
                warn!("Image model {} test timed out.", model);
                ModelStatusInfo { model, status: "Timeout".to_string() }
            } else {
                error!("Image model {} test failed with unexpected error: {}", model, e);
                ModelStatusInfo { model, status: format!("Error: {}", e) } // Simplified error type
            }
        }
    }
}

// --- Cache and State Management ---

// Cache structure for model_status in AppCache needs to be defined or adapted.
// Python: cache["model_status"] = {"statuses": {}, "last_report": ""}
// For Rust, AppCache might need a specific field for this.
// For now, assuming AppCache has methods to store/retrieve these specific pieces of data
// or we pass a more specific cache structure.

pub fn is_model_available(model_name: &str, app_cache: &AppCache) -> bool {
    // This depends on how AppCache is structured.
    // Let's assume AppCache.model_statuses: Option<HashMap<String, String>>
    // For now, this is a conceptual translation.
    // A proper implementation would need to define how model_status is stored in AppCache.
    // If cache is not implemented yet, default to true.
    info!("Cache check for model {} (not fully implemented, defaulting to true)", model_name);
    true // Placeholder
}

pub fn are_max_mode_models_available(app_cache: &AppCache) -> bool {
    let required_models: Vec<String> = CONFIG
        .max_mode_participants
        .iter()
        .cloned()
        .chain(std::iter::once(CONFIG.max_mode_arbiter.clone()))
        .collect();

    for model in required_models {
        if !is_model_available(&model, app_cache) {
            warn!("Max Mode is unavailable because model '{}' is down.", model);
            return false;
        }
    }
    true
}

// This function would modify the cache.
pub fn set_model_failed_in_cache(model_name: &str, _app_cache: &mut AppCache) {
    // Again, depends on AppCache structure.
    // Conceptual: app_cache.model_statuses.entry(model_name.to_string()).or_insert("FAILED".to_string());
    warn!("Circuit Breaker: Model {} marked as FAILED in cache (conceptual).", model_name);
}


pub async fn scheduled_model_test(
    http_client: Arc<HttpClient>,
    db: Arc<Database>,
    // app_cache: Arc<tokio::sync::Mutex<AppCache>>, // If cache needs to be shared and mutable
    _app_cache: Arc<AppCache>, // Assuming cache is read-only for now for simplicity or handled internally
    ai_api_key: Arc<String>,
    ai_api_url: Arc<String>,
) {
    info!("Running scheduled model health check...");

    let mut all_text_models_set = HashSet::new();
    for (_, models) in &CONFIG.model_categories {
        for model in models {
            all_text_models_set.insert(model.clone());
        }
    }
    let all_text_models: Vec<String> = all_text_models_set.into_iter().collect();
    let all_image_models: Vec<String> = CONFIG.image_models.iter().cloned().collect();

    let mut tasks = Vec::new();

    for model in all_text_models {
        tasks.push(tokio::spawn(test_chat_model(
            Arc::clone(&http_client),
            Arc::clone(&ai_api_key),
            Arc::clone(&ai_api_url),
            model, // move ownership
        )));
    }
    for model in all_image_models {
        tasks.push(tokio::spawn(test_image_model(
            Arc::clone(&http_client),
            Arc::clone(&ai_api_key),
            Arc::clone(&ai_api_url),
            model, // move ownership
        )));
    }

    let results_futures = futures::future::join_all(tasks).await;
    let mut current_statuses_map = std::collections::HashMap::new();
    let mut final_results = Vec::new();

    for res in results_futures {
        match res {
            Ok(status_info) => {
                current_statuses_map.insert(status_info.model.clone(), status_info.status.clone());
                final_results.push(status_info);
            }
            Err(e) => {
                error!("Tokio spawn error in scheduled_model_test: {}", e);
                // Handle panicked task, perhaps log and skip
            }
        }
    }

    let mut working_models = Vec::new();
    let mut failed_models_tuples = Vec::new();

    for r_info in &final_results {
        if r_info.status == "OK" {
            working_models.push(r_info.model.clone());
        } else {
            failed_models_tuples.push((r_info.model.clone(), r_info.status.clone()));
        }
    }
    working_models.sort();
    failed_models_tuples.sort_by(|a, b| a.0.cmp(&b.0));

    let timestamp = Utc::now().with_timezone(&*crate::config::MSK_TZ).format("%d.%m.%Y %H:%M:%S МСК").to_string();
    let mut report_text = format!("<b>Отчёт о состоянии моделей от {}</b>\n\n", timestamp);

    if !working_models.is_empty() {
        report_text += &format!("<b>✅ Рабочие модели ({}):</b>\n", working_models.len());
        report_text += &working_models.iter().map(|m| format!("  •  <code>{}</code>", m)).collect::<Vec<_>>().join("\n");
    }
    if !failed_models_tuples.is_empty() {
        report_text += &format!("\n\n<b>❌ Нерабочие модели ({}):</b>\n", failed_models_tuples.len());
        report_text += &failed_models_tuples.iter().map(|(m, s)| format!("  •  <code>{}</code> - {}", m, s)).collect::<Vec<_>>().join("\n");
    }

    // Update DB
    if let Err(e) = db.set_system_state("model_status", &serde_json::to_string(&current_statuses_map).unwrap_or_default()).await {
        error!("Failed to save model_status to DB: {}", e);
    }
    if let Err(e) = db.set_system_state("last_report", &report_text).await {
        error!("Failed to save last_report to DB: {}", e);
    }

    // TODO: Update cache (app_cache.lock().await perhaps)
    // let mut cache_w = app_cache.lock().await;
    // cache_w.model_status_data = Some(current_statuses_map);
    // cache_w.last_report_data = Some(report_text);


    info!("Scheduled model health check finished. State saved to DB. Cache update pending proper implementation.");
}


pub async fn startup_model_check(
    http_client: Arc<HttpClient>,
    db: Arc<Database>,
    // app_cache: Arc<tokio::sync::Mutex<AppCache>>,
    app_cache: Arc<AppCache>, // Placeholder for cache
    ai_api_key: Arc<String>,
    ai_api_url: Arc<String>,
) {
    info!("Performing startup model check...");

    let status_state_opt = match db.get_system_state("model_status").await {
        Ok(Some(s)) => Some(s),
        Ok(None) => None,
        Err(e) => {
            warn!("Failed to get model_status from DB: {}", e); None
        }
    };
    let report_state_opt = match db.get_system_state("last_report").await {
         Ok(Some(s)) => Some(s),
         Ok(None) => None,
         Err(e) => {
            warn!("Failed to get last_report from DB: {}", e); None
         }
    };


    if let (Some(status_state), Some(report_state)) = (status_state_opt, report_state_opt) {
        let status_json = status_state.value.unwrap_or_default();
        let status_timestamp = status_state.updated_at;

        if (Utc::now() - status_timestamp) < ChronoDuration::minutes(10) {
            match serde_json::from_str::<std::collections::HashMap<String, String>>(&status_json) {
                Ok(_statuses) => {
                    // TODO: Update cache
                    // let mut cache_w = app_cache.lock().await;
                    // cache_w.model_status_data = Some(statuses);
                    // cache_w.last_report_data = report_state.value;
                    info!("Loaded recent model status from database. Skipping initial full check. Cache update pending.");
                    return;
                }
                Err(e) => {
                    warn!("Could not parse model_status JSON from DB ({}). Running full check.", e);
                }
            }
        } else {
            info!("DB model status is older than 10 minutes. Running full check.");
        }
    } else {
        info!("No model status found in DB or only partial data. Running full health check...");
    }

    // Fallback to full check
    scheduled_model_test(http_client, db, app_cache, ai_api_key, ai_api_url).await;
}
