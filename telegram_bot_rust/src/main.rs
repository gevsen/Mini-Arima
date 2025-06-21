mod config;
mod db;
mod ai_service;
mod user_service;
mod system_service;
mod handlers;
mod keyboards;
mod states; // Added states module

use std::process::exit;
use std::sync::Arc;
use reqwest::Client as HttpClient;
use teloxide::prelude::*;
use teloxide::dispatching::dialogue::InMemStorage; // For dialogue state storage
use teloxide::utils::command::BotCommands;

use crate::user_service::Cache as AppCache;
use crate::states::State; // Import the dialogue state enum

// Define bot commands - this could be moved to handlers/mod.rs or its own file
#[derive(BotCommands, Clone, Debug)]
#[command(rename_rule = "lowercase", description = "These commands are supported:")]
pub enum Command {
    #[command(description = "display this text.")]
    Help,
    #[command(description = "start the bot or show main menu.")]
    Start,
    #[command(description = "show main menu.")]
    Menu,
    #[command(description = "stop current chat session.")]
    StopChat,
}

// Type alias for the dialogue manager
type MyDialogue = Dialogue<State, InMemStorage<State>>;
// Type alias for handler results used in dialogue FSMs
// type HandlerResult = Result<(), Box<dyn std::error::Error + Send + Sync>>; // From states.rs template
// We'll use anyhow::Result<()> as specified in State's handler_out

#[tokio::main]
async fn main() {
    dotenv::dotenv().ok();
    env_logger::init();

    log::info!("Starting Telegram bot...");

    let bot_token = crate::config::CONFIG.bot_token.clone();
    let api_key = Arc::new(crate::config::CONFIG.api_key.clone());
    let api_url = Arc::new(crate::config::CONFIG.api_url.clone());
    let database_path = crate::config::CONFIG.database_path.clone();

    let bot = Bot::new(bot_token).parse_mode(teloxide::types::ParseMode::Html);

    let db_pool = match db::init_pool(&database_path).await {
        Ok(pool) => Arc::new(pool),
        Err(e) => {
            log::error!("Failed to initialize database pool: {}", e);
            exit(1);
        }
    };

    let http_client = Arc::new(HttpClient::new());
    let app_cache = Arc::new(tokio::sync::Mutex::new(AppCache::new()));
    let dialogue_storage = InMemStorage::<State>::new(); // Dialogue FSM storage

    // Run startup model check
    let db_clone_startup = Arc::clone(&db_pool);
    let http_client_clone_startup = Arc::clone(&http_client);
    let temp_cache_for_startup = Arc::new(AppCache::new());

    tokio::spawn(async move {
        system_service::startup_model_check(
            http_client_clone_startup,
            db_clone_startup,
            temp_cache_for_startup,
            api_key,
            api_url,
        )
        .await;
    });

    match bot.set_my_commands(Command::bot_commands()).await {
        Ok(_) => log::info!("Bot commands set successfully."),
        Err(e) => log::error!("Failed to set bot commands: {}", e),
    }

    // Define the schema for our handlers
    // This combines command handlers, callback handlers, and message handlers (including dialogue state)
    let handler_schema = dptree::entry()
        .branch(Update::filter_message()
            .branch(
                dptree::entry()
                    .filter_command::<Command>()
                    .endpoint(handlers::common_handlers::handle_commands_dialogue)
            )
            .branch( // Handler for messages when in a dialogue state (e.g., captcha answer, chat)
                dptree::endpoint(handlers::common_handlers::handle_dialogue_messages)
            )
        )
        .branch(Update::filter_callback_query().endpoint(handlers::callback_handlers::handle_callback_query_dialogue));

    Dispatcher::builder(bot, handler_schema)
        .dependencies(dptree::deps![
            db_pool,
            http_client, // This ensures http_client is available in the dptree context
            app_cache,
            dialogue_storage
        ])
        .enable_ctrlc_handler()
        .build()
        .dispatch()
        .await;

    log::info!("Bot stopped.");
}
