# app/database.py
import aiosqlite
from datetime import datetime, timedelta, timezone

from app.config import MSK_TZ

class Database:
    """Класс для асинхронной работы с базой данных SQLite."""
    def __init__(self, db_path):
        self.db_path = db_path

    async def _execute(self, query, params=None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(query, params or ())
            await db.commit()

    async def _fetchone(self, query, params=None):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params or ()) as cursor:
                return await cursor.fetchone()

    async def _fetchall(self, query, params=None):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params or ()) as cursor:
                return await cursor.fetchall()

    async def _run_migrations(self):
        """Проверяет и добавляет недостающие столбцы в таблицы."""
        async with aiosqlite.connect(self.db_path) as db:
            # Миграции для таблицы users
            cursor = await db.execute('PRAGMA table_info(users)')
            columns = [row[1] for row in await cursor.fetchall()]

            migrations = {
                'is_blocked': 'INTEGER DEFAULT 0',
                'last_used_model': 'TEXT',
                'is_verified': 'INTEGER DEFAULT 0',
                'has_rewarded_bonus': 'INTEGER DEFAULT 0',
                'last_used_image_model': 'TEXT',
                'user_instruction': 'TEXT',
                'user_temperature': 'REAL'
            }

            for col, col_type in migrations.items():
                if col not in columns:
                    await db.execute(f'ALTER TABLE users ADD COLUMN {col} {col_type}')

            # Миграции для таблицы requests
            cursor = await db.execute('PRAGMA table_info(requests)')
            columns = [row[1] for row in await cursor.fetchall()]
            if 'is_max_mode' not in columns:
                await db.execute('ALTER TABLE requests ADD COLUMN is_max_mode INTEGER DEFAULT 0')

            await db.commit()

    async def create_tables(self):
        """Создает таблицы, если они не существуют."""
        await self._execute('''
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
        ''')
        await self._execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                model TEXT,
                request_date DATE,
                is_max_mode INTEGER DEFAULT 0, -- 0 for normal, 1 for max mode
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        await self._execute('''
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP
            )
        ''')

    async def init_db(self):
        """Инициализирует БД: создает таблицы и запускает миграции."""
        await self.create_tables()
        await self._run_migrations()

    # Методы для работы с системным состоянием
    async def get_system_state(self, key: str):
        query = 'SELECT value, updated_at FROM system_state WHERE key = ?'
        return await self._fetchone(query, (key,))

    async def set_system_state(self, key: str, value: str):
        now_utc = datetime.now(timezone.utc)
        query = '''
            INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        '''
        await self._execute(query, (key, value, now_utc))

    # Методы для работы с пользователями (users)
    async def add_user(self, user_id, username):
        user = await self.get_user(user_id)
        if user:
            if user[1] != username:
                 await self._execute('UPDATE users SET username = ? WHERE user_id = ?', (username.lower() if username else None, user_id))
            return False
        else:
            await self._execute(
                'INSERT INTO users (user_id, username, created_at) VALUES (?, ?, ?)',
                (user_id, username.lower() if username else None, datetime.now(timezone.utc))
            )
            return True

    async def get_user(self, user_id):
        return await self._fetchone('SELECT * FROM users WHERE user_id = ?', (user_id,))

    async def get_user_details(self, user_id):
        query = '''
            SELECT user_id, username, subscription_level, subscription_end, is_blocked,
                   last_used_model, created_at, is_verified, has_rewarded_bonus,
                   last_used_image_model, user_instruction, user_temperature
            FROM users
            WHERE user_id = ?
        '''
        return await self._fetchone(query, (user_id,))

    async def get_user_by_username(self, username):
        return await self._fetchone('SELECT * FROM users WHERE username = ? COLLATE NOCASE', (username.lower(),))

    async def update_subscription(self, user_id, level, days=30):
        now_utc = datetime.now(timezone.utc)
        end_date = now_utc if level == 0 else now_utc + timedelta(days=days)
        await self._execute(
            'UPDATE users SET subscription_level = ?, subscription_end = ? WHERE user_id = ?',
            (level, end_date.isoformat(), user_id)
        )

    async def set_last_used_model(self, user_id, model_name):
        await self._execute('UPDATE users SET last_used_model = ? WHERE user_id = ?', (model_name, user_id))

    async def set_last_used_image_model(self, user_id, model_name):
        await self._execute('UPDATE users SET last_used_image_model = ? WHERE user_id = ?', (model_name, user_id))

    async def set_user_instruction(self, user_id, instruction):
        await self._execute('UPDATE users SET user_instruction = ? WHERE user_id = ?', (instruction, user_id))

    async def set_user_temperature(self, user_id, temperature):
        await self._execute('UPDATE users SET user_temperature = ? WHERE user_id = ?', (temperature, user_id))

    async def block_user(self, user_id, block=True):
        await self._execute('UPDATE users SET is_blocked = ? WHERE user_id = ?', (1 if block else 0, user_id))

    async def set_user_verified(self, user_id, status: bool = True):
        await self._execute('UPDATE users SET is_verified = ? WHERE user_id = ?', (1 if status else 0, user_id))

    async def set_reward_bonus(self, user_id):
        await self._execute('UPDATE users SET has_rewarded_bonus = 1 WHERE user_id = ?', (user_id,))

    async def get_all_user_ids(self):
        rows = await self._fetchall('SELECT user_id FROM users')
        return [row[0] for row in rows]

    async def get_users_paginated(self, page: int = 1, page_size: int = 1):
        offset = (page - 1) * page_size
        query = 'SELECT user_id FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?'
        return await self._fetchall(query, (page_size, offset))

    async def get_user_count(self):
        result = await self._fetchone('SELECT COUNT(*) FROM users')
        return result[0] if result else 0

    async def get_subscription_stats(self):
        stats = {}
        for level in [0, 1, 2, 3]: # Включая уровень 3 для Max
            result = await self._fetchone(
                'SELECT COUNT(*) FROM users WHERE subscription_level = ?', (level,)
            )
            stats[level] = result[0] if result else 0
        return stats

    # Методы для работы с запросами (requests)
    async def get_user_requests_today(self, user_id: int, is_max_mode: bool = False):
        """Получает количество обычных или Max Mode запросов за сегодня."""
        today = datetime.now(MSK_TZ).date()
        result = await self._fetchone(
            'SELECT COUNT(*) FROM requests WHERE user_id = ? AND request_date = ? AND is_max_mode = ?',
            (user_id, today, 1 if is_max_mode else 0)
        )
        return result[0] if result else 0

    async def add_request(self, user_id, model, is_max_mode=False):
        """Добавляет запись о новом запросе."""
        today = datetime.now(MSK_TZ).date()
        await self._execute(
            'INSERT INTO requests (user_id, model, request_date, is_max_mode) VALUES (?, ?, ?, ?)',
            (user_id, model, today, 1 if is_max_mode else 0)
        )