use chrono::{FixedOffset, TimeZone};
use dotenv::dotenv;
use once_cell::sync::Lazy;
use serde::Deserialize;
use std::collections::HashMap;
use std::env;

// --- Временная зона ---
pub static MSK_TZ: Lazy<FixedOffset> = Lazy::new(|| FixedOffset::east_opt(3 * 3600).unwrap());

// --- Структуры для типизации ---
#[derive(Deserialize, Debug, Clone)]
pub struct RewardChannel {
    pub id: String,
    pub name: String,
}

#[derive(Deserialize, Debug, Clone)]
pub struct LimitDetails {
    pub daily: i32,
    pub max_mode: i32,
}

#[derive(Debug, Clone)]
pub struct AppConfig {
    // --- Основные настройки ---
    pub bot_token: String,
    pub api_key: String,
    pub api_url: String,
    pub database_path: String,

    // --- Администраторы и контакты ---
    pub admin_ids: Vec<i64>,
    pub sub_contact: String,
    pub support_contact: String,

    // --- Настройки наград и групп ---
    pub reward_channels: Vec<RewardChannel>,
    pub group_text_trigger: String,
    pub group_image_trigger: String,

    // --- Настройки моделей и AI ---
    pub global_system_prompt: String,
    pub default_temperature: f64,
    pub default_text_model: String,
    pub default_image_model: String,

    // --- Настройки Max Mode ---
    pub max_mode_participants: Vec<String>,
    pub max_mode_arbiter: String,

    // --- Модели и уровни доступа ---
    pub model_categories: HashMap<String, Vec<String>>,
    pub models_access: HashMap<String, Vec<String>>,
    pub image_models: Vec<String>,

    // --- Лимиты и подписки ---
    pub limits: HashMap<i32, LimitDetails>,
    pub reward_limit: i32,
    pub prices: HashMap<i32, i32>,

    // --- Капча ---
    pub captcha_variants: Vec<(String, String)>,
}

fn get_env_var(key: &str) -> String {
    env::var(key).unwrap_or_else(|_| panic!("Missing environment variable: {}", key))
}

fn get_env_var_opt(key: &str) -> Option<String> {
    env::var(key).ok()
}

fn get_env_var_default(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

pub static CONFIG: Lazy<AppConfig> = Lazy::new(|| {
    dotenv().ok(); // Загружаем .env файл

    let admin_ids_str = get_env_var("ADMIN_IDS");
    let admin_ids = admin_ids_str
        .split(',')
        .map(|s| s.trim().parse::<i64>().expect("Invalid ADMIN_ID"))
        .collect();

    let mut reward_channels = Vec::new();
    if let (Some(id1), Some(name1)) = (
        get_env_var_opt("REWARD_CHANNEL_1_ID"),
        get_env_var_opt("REWARD_CHANNEL_1_NAME"),
    ) {
        if !id1.is_empty() && !name1.is_empty() {
            reward_channels.push(RewardChannel { id: id1, name: name1 });
        }
    }
    if let (Some(id2), Some(name2)) = (
        get_env_var_opt("REWARD_CHANNEL_2_ID"),
        get_env_var_opt("REWARD_CHANNEL_2_NAME"),
    ) {
        if !id2.is_empty() && !name2.is_empty() {
            reward_channels.push(RewardChannel { id: id2, name: name2 });
        }
    }

    let model_categories_map: HashMap<String, Vec<String>> = [
        (
            "OpenAI".to_string(),
            vec![
                "gpt-4.5-preview".to_string(),
                "gpt-4.1".to_string(),
                "o4-mini".to_string(),
                "chatgpt-4o-latest".to_string(),
            ],
        ),
        (
            "DeepSeek".to_string(),
            vec![
                "deepseek-chat-v3-0324".to_string(),
                "deepseek-r1-0528".to_string(),
            ],
        ),
        (
            "Meta".to_string(),
            vec!["llama-3.1-nemotron-ultra-253b-v1".to_string()],
        ),
        ("Alibaba".to_string(), vec!["qwen3-235b-a22b".to_string()]),
        (
            "Microsoft".to_string(),
            vec!["phi-4-reasoning-plus".to_string()],
        ),
        (
            "xAI".to_string(),
            vec!["grok-3".to_string(), "grok-3-mini".to_string()],
        ),
        (
            "Anthropic".to_string(),
            vec!["claude-3.7-sonnet".to_string()],
        ),
    ]
    .iter()
    .cloned()
    .collect();

    let mut premium_models: Vec<String> = model_categories_map
        .values()
        .flatten()
        .cloned()
        .collect::<std::collections::HashSet<_>>() // To make them unique
        .into_iter()
        .collect();
    premium_models.sort(); // For consistent order, though not strictly necessary

    let models_access_map: HashMap<String, Vec<String>> = [
        (
            "free".to_string(),
            vec![
                "deepseek-chat-v3-0324".to_string(),
                "gpt-4.1".to_string(),
                "chatgpt-4o-latest".to_string(),
            ],
        ),
        (
            "standard".to_string(),
            vec![
                "deepseek-chat-v3-0324".to_string(),
                "gpt-4.1".to_string(),
                "chatgpt-4o-latest".to_string(),
                "llama-3.1-nemotron-ultra-253b-v1".to_string(),
                "qwen3-235b-a22b".to_string(),
                "phi-4-reasoning-plus".to_string(),
                "grok-3-mini".to_string(),
            ],
        ),
        ("premium".to_string(), premium_models),
    ]
    .iter()
    .cloned()
    .collect();

    let limits_map: HashMap<i32, LimitDetails> = [
        (0, LimitDetails { daily: 3, max_mode: 0 }),
        (1, LimitDetails { daily: 40, max_mode: 0 }),
        (2, LimitDetails { daily: 100, max_mode: 0 }),
        (3, LimitDetails { daily: 100, max_mode: 5 }),
    ]
    .iter()
    .cloned()
    .collect();

    let prices_map: HashMap<i32, i32> =
        [(1, 150), (2, 350), (3, 600)].iter().cloned().collect();

    let captcha_variants_vec: Vec<(String, String)> = vec![
        ("Чему равен корень из 9?".to_string(), "3".to_string()),
        ("Сколько будет 2 + 2 * 2?".to_string(), "6".to_string()),
        ("Столица Франции?".to_string(), "париж".to_string()),
        ("Сколько букв в слове 'ТЕЛЕГРАМ'?".to_string(), "8".to_string()),
        ("Напишите число 'пять' цифрой.".to_string(), "5".to_string()),
    ];

    AppConfig {
        bot_token: get_env_var("BOT_TOKEN"),
        api_key: get_env_var("API_KEY"),
        api_url: get_env_var("API_URL"),
        database_path: get_env_var_default("DATABASE", "database.db"),
        admin_ids,
        sub_contact: get_env_var_default("SUB_CONTACT", "gevsen"),
        support_contact: get_env_var_default("SUPPORT_CONTACT", "gevsen"),
        reward_channels,
        group_text_trigger: get_env_var_default("GROUP_TEXT_TRIGGER", ".text"),
        group_image_trigger: get_env_var_default("GROUP_IMAGE_TRIGGER", ".image"),
        global_system_prompt: "Ты - MiniArima, продвинутый GenAI ассистент.".to_string(),
        default_temperature: 0.7,
        default_text_model: "chatgpt-4o-latest".to_string(),
        default_image_model: "gpt-image-1".to_string(),
        max_mode_participants: vec![
            "grok-3".to_string(),
            "gpt-4.1".to_string(),
            "deepseek-chat-v3-0324".to_string(),
            "gpt-4.5-preview".to_string(),
            "chatgpt-4o-latest".to_string(),
            "claude-3.7-sonnet".to_string(),
        ],
        max_mode_arbiter: "deepseek-r1-0528".to_string(),
        model_categories: model_categories_map,
        models_access: models_access_map,
        image_models: vec!["gpt-image-1".to_string(), "flux-1.1-pro".to_string()],
        limits: limits_map,
        reward_limit: 7,
        prices: prices_map,
        captcha_variants: captcha_variants_vec,
    }
});

// Main function to load and print the config (for testing purposes)
/*
pub fn main() {
    // Load .env file if you haven't already
    dotenv().ok();
    // Access the configuration
    println!("Bot Token: {}", CONFIG.bot_token);
    println!("Admin IDs: {:?}", CONFIG.admin_ids);
    println!("Default Text Model: {}", CONFIG.default_text_model);
    // Add more prints as needed
}
*/

// To make this module usable by main.rs or other modules
pub fn load_config() -> &'static AppConfig {
    &CONFIG
}
