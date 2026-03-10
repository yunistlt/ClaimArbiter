import logging
import os
import sqlite3
from typing import Set
import json

logger = logging.getLogger(__name__)

class AccessControl:
    _instance = None
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    DB_PATH = os.getenv("ACCESS_CONTROL_DB_PATH", os.path.join(BASE_DIR, "access_control.db"))
    LEGACY_FILE_PATH = os.path.join(BASE_DIR, "allowed_users.json")
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AccessControl, cls).__new__(cls)
            cls._instance.users = set()
            cls._instance.chats = set()
            cls._instance._init_db()
            cls._instance.load_data()
        return cls._instance

    def _get_connection(self):
        return sqlite3.connect(self.DB_PATH)

    def _init_db(self):
        """Create tables and migrate legacy JSON data when possible."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS allowed_users (
                        user_id INTEGER PRIMARY KEY,
                        first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS allowed_chats (
                        chat_id INTEGER PRIMARY KEY,
                        first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.commit()
            self._migrate_legacy_json_if_needed()
        except Exception as e:
            logger.error(f"Error initializing access DB: {e}")

    def _migrate_legacy_json_if_needed(self):
        """
        Migrate old allowed_users.json into DB once if DB is empty.
        """
        if not os.path.exists(self.LEGACY_FILE_PATH):
            return

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM allowed_users")
                users_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM allowed_chats")
                chats_count = cursor.fetchone()[0]

                if users_count > 0 or chats_count > 0:
                    return

            with open(self.LEGACY_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            legacy_users = data.get("allowed_ids", [])
            legacy_chats = data.get("allowed_chats", [])

            with self._get_connection() as conn:
                cursor = conn.cursor()
                for user_id in legacy_users:
                    cursor.execute("INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)", (int(user_id),))
                for chat_id in legacy_chats:
                    cursor.execute("INSERT OR IGNORE INTO allowed_chats (chat_id) VALUES (?)", (int(chat_id),))
                conn.commit()

            logger.info(
                f"Migrated legacy access data to DB: users={len(legacy_users)}, chats={len(legacy_chats)}"
            )
        except Exception as e:
            logger.error(f"Error migrating legacy access data: {e}")

    def load_data(self):
        """Load allowed users and chats from SQLite DB."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM allowed_users")
                self.users = {row[0] for row in cursor.fetchall()}
                cursor.execute("SELECT chat_id FROM allowed_chats")
                self.chats = {row[0] for row in cursor.fetchall()}
            logger.info(f"Loaded {len(self.users)} allowed users and {len(self.chats)} chats.")
        except Exception as e:
            logger.error(f"Error loading access data: {e}")
            self.users = set()
            self.chats = set()

    def save_data(self):
        """No-op: DB writes happen incrementally in add_user/add_chat."""
        return

    def add_user(self, user_id: int):
        """Add a user to allowlist and persist to DB."""
        if user_id in self.users:
            return

        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)", (int(user_id),))
                conn.commit()
            self.users.add(user_id)
            logger.info(f"New user authorized: {user_id}")
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")

    def add_chat(self, chat_id: int):
        """Add a chat to the known work chats list and persist to DB."""
        if chat_id in self.chats:
            return

        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO allowed_chats (chat_id) VALUES (?)", (int(chat_id),))
                conn.commit()
            self.chats.add(chat_id)
            logger.info(f"New work chat authorized: {chat_id}")
        except Exception as e:
            logger.error(f"Error adding chat {chat_id}: {e}")

    def is_user_known(self, user_id: int) -> bool:
        """Check if user is already known/cached."""
        return user_id in self.users

    def get_known_chats(self) -> Set[int]:
        """Return list of known work chats."""
        return self.chats
