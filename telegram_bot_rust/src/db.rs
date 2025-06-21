use crate::config; // To access MSK_TZ and other config if needed in future
use chrono::{DateTime, NaiveDate, Utc, Duration};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePool, SqlitePoolOptions, SqliteRow};
use sqlx::{Error as SqlxError, FromRow, Row};
use std::collections::HashMap;
use std::str::FromStr;

// --- Structs for table rows ---
#[derive(Debug, FromRow, Clone)]
pub struct User {
    pub user_id: i64,
    pub username: Option<String>,
    pub subscription_level: i32,
    pub subscription_end: Option<DateTime<Utc>>,
    pub is_blocked: i32, // Representing BOOLEAN as INTEGER for SQLite
    pub is_verified: i32,
    pub has_rewarded_bonus: i32,
    pub last_used_model: Option<String>,
    pub last_used_image_model: Option<String>,
    pub user_instruction: Option<String>,
    pub user_temperature: Option<f64>, // REAL
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, FromRow, Clone)]
pub struct Request {
    pub id: i32, // Assuming AUTOINCREMENT maps to i32 or i64
    pub user_id: i64,
    pub model: Option<String>,
    pub request_date: NaiveDate, // DATE
    pub is_max_mode: i32,        // INTEGER DEFAULT 0
}

#[derive(Debug, FromRow, Clone)]
pub struct SystemState {
    pub key: String,
    pub value: Option<String>,
    pub updated_at: DateTime<Utc>,
}

// --- Database struct ---
#[derive(Clone)]
pub struct Database {
    pool: SqlitePool,
}

impl Database {
    pub async fn new(db_path: &str) -> Result<Self, SqlxError> {
        let connect_options = SqliteConnectOptions::from_str(db_path)?
            .create_if_missing(true); // Create DB file if it doesn't exist

        let pool = SqlitePoolOptions::new()
            .max_connections(5) // Configure as needed
            .connect_with(connect_options)
            .await?;
        Ok(Database { pool })
    }

    async fn run_migrations(&self) -> Result<(), SqlxError> {
        // Get current columns for 'users' table
        let rows: Vec<SqliteRow> = sqlx::query("PRAGMA table_info(users)").fetch_all(&self.pool).await?;
        let mut columns = Vec::new();
        for row in rows {
            columns.push(row.try_get::<String, _>("name")?);
        }

        let user_migrations: HashMap<&str, &str> = [
            ("is_blocked", "INTEGER DEFAULT 0"),
            ("last_used_model", "TEXT"),
            ("is_verified", "INTEGER DEFAULT 0"),
            ("has_rewarded_bonus", "INTEGER DEFAULT 0"),
            ("last_used_image_model", "TEXT"),
            ("user_instruction", "TEXT"),
            ("user_temperature", "REAL"),
        ]
        .iter().cloned().collect();

        for (col, col_type) in user_migrations {
            if !columns.contains(&col.to_string()) {
                let query_str = format!("ALTER TABLE users ADD COLUMN {} {}", col, col_type);
                sqlx::query(&query_str).execute(&self.pool).await?;
            }
        }

        // Get current columns for 'requests' table
        let rows_req: Vec<SqliteRow> = sqlx::query("PRAGMA table_info(requests)").fetch_all(&self.pool).await?;
        let mut columns_req = Vec::new();
        for row in rows_req {
            columns_req.push(row.try_get::<String, _>("name")?);
        }
        if !columns_req.contains(&"is_max_mode".to_string()) {
            sqlx::query("ALTER TABLE requests ADD COLUMN is_max_mode INTEGER DEFAULT 0")
                .execute(&self.pool)
                .await?;
        }

        Ok(())
    }

    pub async fn create_tables(&self) -> Result<(), SqlxError> {
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                subscription_level INTEGER DEFAULT 0,
                subscription_end TIMESTAMP,
                is_blocked INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                has_rewarded_bonus INTEGER DEFAULT 0,
                last_used_model TEXT,
                last_used_image_model TEXT,
                user_instruction TEXT,
                user_temperature REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            "#,
        )
        .execute(&self.pool)
        .await?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                model TEXT,
                request_date DATE,
                is_max_mode INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            "#,
        )
        .execute(&self.pool)
        .await?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP
            )
            "#,
        )
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn init_db(&self) -> Result<(), SqlxError> {
        self.create_tables().await?;
        self.run_migrations().await?;
        Ok(())
    }

    // --- System State Methods ---
    pub async fn get_system_state(&self, key: &str) -> Result<Option<SystemState>, SqlxError> {
        sqlx::query_as("SELECT key, value, updated_at FROM system_state WHERE key = ?")
            .bind(key)
            .fetch_optional(&self.pool)
            .await
    }

    pub async fn set_system_state(&self, key: &str, value: &str) -> Result<(), SqlxError> {
        let now_utc = Utc::now();
        sqlx::query(
            r#"
            INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            "#,
        )
        .bind(key)
        .bind(value)
        .bind(now_utc)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    // --- User Methods ---
    pub async fn add_user(&self, user_id: i64, username: Option<&str>) -> Result<bool, SqlxError> {
        let existing_user: Option<User> = self.get_user(user_id).await?;

        let mut uname_processed: Option<String> = None;
        if let Some(u) = username {
            uname_processed = Some(u.to_lowercase());
        }


        if let Some(user) = existing_user {
            if user.username != uname_processed {
                sqlx::query("UPDATE users SET username = ? WHERE user_id = ?")
                    .bind(&uname_processed)
                    .bind(user_id)
                    .execute(&self.pool)
                    .await?;
            }
            Ok(false) // User existed
        } else {
            let now_utc = Utc::now();
            sqlx::query(
                "INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)",
            )
            .bind(user_id)
            .bind(uname_processed)
            .bind(now_utc)
            .execute(&self.pool)
            .await?;
            Ok(true) // User was added
        }
    }

    pub async fn get_user(&self, user_id: i64) -> Result<Option<User>, SqlxError> {
        sqlx::query_as("SELECT * FROM users WHERE user_id = ?")
            .bind(user_id)
            .fetch_optional(&self.pool)
            .await
    }

    // get_user_details is essentially the same as get_user if User struct contains all fields
    // If specific fields were needed, a new struct and query_as would be used.
    // For now, get_user suffices.

    pub async fn get_user_by_username(&self, username: &str) -> Result<Option<User>, SqlxError> {
        sqlx::query_as("SELECT * FROM users WHERE username = ? COLLATE NOCASE")
            .bind(username.to_lowercase())
            .fetch_optional(&self.pool)
            .await
    }

    pub async fn update_subscription(&self, user_id: i64, level: i32, days: i64) -> Result<(), SqlxError> {
        let now_utc = Utc::now();
        let end_date = if level == 0 {
            now_utc
        } else {
            now_utc + Duration::days(days)
        };
        sqlx::query(
            "UPDATE users SET subscription_level = ?, subscription_end = ? WHERE user_id = ?",
        )
        .bind(level)
        .bind(end_date)
        .bind(user_id)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn set_last_used_model(&self, user_id: i64, model_name: &str) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET last_used_model = ? WHERE user_id = ?")
            .bind(model_name)
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn set_last_used_image_model(&self, user_id: i64, model_name: &str) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET last_used_image_model = ? WHERE user_id = ?")
            .bind(model_name)
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn set_user_instruction(&self, user_id: i64, instruction: Option<&str>) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET user_instruction = ? WHERE user_id = ?")
            .bind(instruction)
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn set_user_temperature(&self, user_id: i64, temperature: Option<f64>) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET user_temperature = ? WHERE user_id = ?")
            .bind(temperature)
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn block_user(&self, user_id: i64, block: bool) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET is_blocked = ? WHERE user_id = ?")
            .bind(if block { 1 } else { 0 })
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn set_user_verified(&self, user_id: i64, status: bool) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET is_verified = ? WHERE user_id = ?")
            .bind(if status { 1 } else { 0 })
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn set_reward_bonus(&self, user_id: i64) -> Result<(), SqlxError> {
        sqlx::query("UPDATE users SET has_rewarded_bonus = 1 WHERE user_id = ?")
            .bind(user_id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn get_all_user_ids(&self) -> Result<Vec<i64>, SqlxError> {
        let rows: Vec<(i64,)> = sqlx::query_as("SELECT user_id FROM users")
            .fetch_all(&self.pool)
            .await?;
        Ok(rows.into_iter().map(|row| row.0).collect())
    }

    pub async fn get_users_paginated(&self, page: i64, page_size: i64) -> Result<Vec<i64>, SqlxError> {
        let offset = (page - 1) * page_size;
        let rows: Vec<(i64,)> = sqlx::query_as("SELECT user_id FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?")
            .bind(page_size)
            .bind(offset)
            .fetch_all(&self.pool)
            .await?;
        Ok(rows.into_iter().map(|row| row.0).collect())
    }

    pub async fn get_user_count(&self) -> Result<i64, SqlxError> {
        let (count,): (i64,) = sqlx::query_as("SELECT COUNT(*) FROM users")
            .fetch_one(&self.pool)
            .await?;
        Ok(count)
    }

    pub async fn get_subscription_stats(&self) -> Result<HashMap<i32, i64>, SqlxError> {
        let mut stats = HashMap::new();
        for level in [0, 1, 2, 3] { // Including Max level 3
            let (count,): (i64,) = sqlx::query_as(
                "SELECT COUNT(*) FROM users WHERE subscription_level = ?",
            )
            .bind(level)
            .fetch_one(&self.pool)
            .await?;
            stats.insert(level, count);
        }
        Ok(stats)
    }

    // --- Request Methods ---
    pub async fn get_user_requests_today(&self, user_id: i64, is_max_mode: bool) -> Result<i64, SqlxError> {
        let today_msk = Utc::now().with_timezone(&*config::MSK_TZ).date_naive();
        let (count,): (i64,) = sqlx::query_as(
            "SELECT COUNT(*) FROM requests WHERE user_id = ? AND request_date = ? AND is_max_mode = ?",
        )
        .bind(user_id)
        .bind(today_msk)
        .bind(if is_max_mode { 1 } else { 0 })
        .fetch_one(&self.pool)
        .await?;
        Ok(count)
    }

    pub async fn add_request(&self, user_id: i64, model: Option<&str>, is_max_mode: bool) -> Result<(), SqlxError> {
        let today_msk = Utc::now().with_timezone(&*config::MSK_TZ).date_naive();
        sqlx::query(
            "INSERT INTO requests (user_id, model, request_date, is_max_mode) VALUES (?, ?, ?, ?)",
        )
        .bind(user_id)
        .bind(model)
        .bind(today_msk)
        .bind(if is_max_mode { 1 } else { 0 })
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

// Example of how to use (will be moved to main.rs or tests)
/*
async fn test_db_operations() -> Result<(), SqlxError> {
    // Ensure .env is loaded for config::CONFIG.database_path
    dotenv::dotenv().ok();
    let db_path = &config::CONFIG.database_path;

    let db = Database::new(db_path).await?;
    db.init_db().await?;

    // Test add_user
    let new_user_added = db.add_user(12345, Some("testuser")).await?;
    println!("New user added: {}", new_user_added);
    let user_details = db.get_user(12345).await?;
    println!("User details: {:?}", user_details);

    // Test add_request
    db.add_request(12345, Some("gpt-4"), false).await?;
    let requests_today = db.get_user_requests_today(12345, false).await?;
    println!("Requests today for user 12345: {}", requests_today);

    // Test system state
    db.set_system_state("model_status_gpt-4", "online").await?;
    let system_state = db.get_system_state("model_status_gpt-4").await?;
    println!("System state for gpt-4: {:?}", system_state);

    Ok(())
}

#[tokio::main]
async fn main() {
    if let Err(e) = test_db_operations().await {
        eprintln!("Database operation failed: {}", e);
    }
}
*/

// Function to easily get a DB pool, to be used in main.rs
pub async fn init_pool(database_url: &str) -> Result<Database, SqlxError> {
    let db = Database::new(database_url).await?;
    db.init_db().await?; // Initialize tables and run migrations
    Ok(db)
}
