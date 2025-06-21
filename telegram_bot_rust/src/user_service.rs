use crate::config::{AppConfig, CONFIG, CaptchaVariant};
use crate::db::{Database, User as DbUser}; // Renamed to avoid conflict with Teloxide User
use chrono::{DateTime, Utc};
use log::{debug, info, warn};
use rand::seq::SliceRandom;
use std::collections::HashMap; // For cache placeholder
use teloxide::macros::BotCommands; // For potential future use with bot commands related to user
use teloxide::payloads::SendMessageSetters;
use teloxide::requests::Requester;
use teloxide::types::{ChatId, User as TeloxideUser}; // Teloxide's User struct
use teloxide::Bot;
// For FSM: Teloxide uses Dialogues. We'll need to define states for Captcha.
// For now, this service won't directly interact with Teloxide's FSM/Dialogue,
// but the handler calling it will. So, we'll pass necessary state-related data.

// --- Cache Placeholder ---
// In a real application, this would be a more robust caching solution like `cached` crate
// or a shared `DashMap` or `moka` for concurrent access.
// For now, a simple HashMap to illustrate the concept, but it's not thread-safe for real use.
// The Python version's cache is also a simple dict, likely not thread-safe with `asyncio` if not careful.
pub struct Cache {
    // Simulating TTLCache behavior would require more complex logic here.
    // For now, just a simple HashMap.
    // Key: user_id, Value: (DbUser, timestamp for TTL) - not implemented yet
    pub user_details: Option<HashMap<i64, DbUser>>, // Made Option to match Python's cache.get("user_details")
}

impl Cache {
    pub fn new() -> Self {
        Cache {
            user_details: Some(HashMap::new()),
        }
    }

    // This is a simplified version. A real TTLCache would handle expiration.
    pub fn get_user_details(&self, user_id: i64) -> Option<&DbUser> {
        self.user_details.as_ref()?.get(&user_id)
    }

    pub fn set_user_details(&mut self, user_id: i64, details: DbUser) {
        if let Some(cache_map) = self.user_details.as_mut() {
            cache_map.insert(user_id, details);
        }
    }

    pub fn invalidate_user_cache(&mut self, user_id: i64) {
        if let Some(cache_map) = self.user_details.as_mut() {
            if cache_map.remove(&user_id).is_some() {
                debug!("Cache invalidated for user {}", user_id);
            }
        }
    }
}


// --- Captcha State (simplified for now) ---
// In Teloxide, this would typically be part of a Dialogue enum.
#[derive(Clone, Debug)]
pub enum CaptchaState {
    Pending(String), // Stores the expected answer
    // Verified (not explicitly needed here, absence of state means verified or not started)
}

// --- Service Functions ---

// Corresponds to Python's send_captcha
// Note: In Rust/Teloxide, sending messages and managing state is usually done in handlers.
// This function shows how it *could* be structured if a service needs to send a message.
// `current_captcha_answer` would be stored by the handler in its dialogue state.
pub async fn prepare_captcha_data() -> Result<(String, String, CaptchaVariant), String> {
    let chosen_variant = CONFIG
        .captcha_variants
        .choose(&mut rand::thread_rng())
        .ok_or_else(|| "No captcha variants configured".to_string())?;
    Ok((
        format!(
            "Чтобы начать, пожалуйста, решите простую задачу:\n<b>{}</b>\n\nНапишите ответ в чат.",
            chosen_variant.0
        ),
        chosen_variant.1.clone(), // The answer
        chosen_variant.clone() // The full variant for logging or other purposes
    ))
}


// Corresponds to Python's get_user_details_cached
// #[cached(
//     map_type = "LruCache<i64, Option<DbUser>>", // Example, needs `cached` crate
//     create = "{ LruCache::new(1000) }",
//     convert = r#"{ user_id }"#,
//     time = 300, // TTL in seconds
//     result = true // Cache Result<Option<DbUser>, _>
// )]
// The above `cached` macro is how it might look. For now, manual cache interaction.
pub async fn get_user_details_cached(
    user_id: i64,
    db: &Database,
    cache: &mut Cache, // Mutable because our simple cache might insert
) -> Result<Option<DbUser>, sqlx::Error> {
    if let Some(details) = cache.get_user_details(user_id).cloned() { // Cloned to avoid lifetime issues with mutable borrow later
        debug!("User details for {} found in cache.", user_id);
        return Ok(Some(details));
    }

    debug!("User details for {} not in cache. Fetching from DB.", user_id);
    let details = db.get_user(user_id).await?; // get_user fetches all details like python's get_user_details
    if let Some(ref d) = details {
        cache.set_user_details(user_id, d.clone());
    }
    Ok(details)
}


pub async fn get_user_level(user_id: i64, db: &Database, cache: &mut Cache) -> Result<i32, sqlx::Error> {
    if CONFIG.admin_ids.contains(&user_id) {
        return Ok(3); // Admins have max level
    }

    // Use the cached version to get user details
    let user_opt = get_user_details_cached(user_id, db, cache).await?;

    match user_opt {
        Some(user) => {
            let level = user.subscription_level;
            if level > 0 {
                if let Some(end_date_utc) = user.subscription_end {
                    if end_date_utc < Utc::now() {
                        info!("Subscription expired for user {}. Setting level to 0.", user_id);
                        // Note: Python version calls db.update_subscription here.
                        // Consider if this function should have side effects or just calculate.
                        // For now, mirroring Python's side effect.
                        db.update_subscription(user_id, 0, 0).await?; // 0 days for level 0
                        cache.invalidate_user_cache(user_id); // Invalidate after update
                        return Ok(0);
                    }
                } else {
                    // Has a level > 0 but no end_date, treat as expired or invalid.
                    // This case might indicate data inconsistency.
                    warn!("User {} has subscription level {} but no end_date. Treating as level 0.", user_id, level);
                    return Ok(0);
                }
            }
            Ok(level)
        }
        None => Ok(0), // User not found, default to free tier
    }
}

pub async fn get_user_limits(
    user_id: i64,
    db: &Database,
    cache: &mut Cache,
) -> Result<(i32, i32), sqlx::Error> {
    let level = get_user_level(user_id, db, cache).await?;

    if CONFIG.admin_ids.contains(&user_id) {
        // Using a very large number to simulate infinity for i32
        return Ok((i32::MAX, i32::MAX));
    }

    if level == 0 {
        if let Some(details) = get_user_details_cached(user_id, db, cache).await? {
            if details.has_rewarded_bonus == 1 {
                return Ok((CONFIG.reward_limit, 0));
            }
        }
    }

    let plan_limits = CONFIG.limits.get(&level).cloned().unwrap_or_else(|| {
        warn!("No limits defined for level {}. Defaulting to 0,0.", level);
        crate::config::LimitDetails { daily: 0, max_mode: 0 } // Ensure LimitDetails is accessible
    });
    Ok((plan_limits.daily, plan_limits.max_mode))
}

// Corresponds to Python's check_authentication
// The actual sending of captcha and state management will be in the Teloxide handler.
// This service function just checks the DB status.
pub async fn is_user_verified_in_db(user_id: i64, db: &Database, cache: &mut Cache) -> Result<bool, sqlx::Error> {
    if let Some(details) = get_user_details_cached(user_id, db, cache).await? {
        Ok(details.is_verified == 1)
    } else {
        Ok(false) // User not found, so not verified
    }
}


pub async fn get_user_id_from_input(input_str: &str, db: &Database) -> Result<Option<i64>, sqlx::Error> {
    if let Some(username) = input_str.strip_prefix('@') {
        match db.get_user_by_username(username).await? {
            Some(user) => Ok(Some(user.user_id)),
            None => Ok(None),
        }
    } else {
        match input_str.parse::<i64>() {
            Ok(id) => Ok(Some(id)),
            Err(_) => Ok(None),
        }
    }
}
