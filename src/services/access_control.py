import logging
import os
import sqlite3
from typing import Set
import json
from services.supabase_storage import SupabaseStorage

logger = logging.getLogger(__name__)

class AccessControl:
    _instance = None
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    DB_PATH = os.getenv("ACCESS_CONTROL_DB_PATH", os.path.join(DATA_DIR, "access_control.db"))
    LEGACY_FILE_PATH = os.path.join(BASE_DIR, "allowed_users.json")
    _supabase = SupabaseStorage()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AccessControl, cls).__new__(cls)
            cls._instance.users = set()
            cls._instance.chats = set()
            cls._instance.active_chat_by_user = {}
            cls._instance.user_profiles = {}
            cls._instance._init_db()
            cls._instance.load_data()
        return cls._instance

    def _get_connection(self):
        return sqlite3.connect(self.DB_PATH)

    def _init_db(self):
        """Create tables and migrate legacy JSON data when possible."""
        os.makedirs(os.path.dirname(self.DB_PATH), exist_ok=True)
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
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS active_user_chats (
                        user_id INTEGER PRIMARY KEY,
                        chat_id INTEGER NOT NULL,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        user_id INTEGER PRIMARY KEY,
                        full_name TEXT,
                        role TEXT NOT NULL DEFAULT 'employee',
                        department TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
                cursor.execute("SELECT user_id, chat_id FROM active_user_chats")
                self.active_chat_by_user = {row[0]: row[1] for row in cursor.fetchall()}
                cursor.execute("SELECT user_id, full_name, role, department FROM user_profiles")
                self.user_profiles = {
                    row[0]: {
                        "user_id": row[0],
                        "full_name": row[1],
                        "role": row[2] or "employee",
                        "department": row[3],
                    }
                    for row in cursor.fetchall()
                }
            logger.info(f"Loaded {len(self.users)} allowed users and {len(self.chats)} chats.")
        except Exception as e:
            logger.error(f"Error loading access data: {e}")
            self.users = set()
            self.chats = set()
            self.active_chat_by_user = {}
            self.user_profiles = {}

    def save_data(self):
        """No-op: DB writes happen incrementally in add_user/add_chat."""
        return

    def add_user(self, user_id: int, full_name: str | None = None):
        """Add a user to allowlist and persist to DB."""
        if user_id in self.users:
            if full_name:
                self.set_user_profile(user_id=user_id, full_name=full_name)
            return

        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)", (int(user_id),))
                conn.execute(
                    "INSERT INTO user_profiles(user_id, full_name, role) VALUES(?, ?, 'employee') "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "full_name = COALESCE(excluded.full_name, user_profiles.full_name), "
                    "updated_at = CURRENT_TIMESTAMP",
                    (int(user_id), full_name),
                )
                conn.commit()
            self.users.add(user_id)
            self.user_profiles[user_id] = {
                "user_id": user_id,
                "full_name": full_name,
                "role": "employee",
                "department": None,
            }
            self._supabase.upsert_work_user(user_id=user_id, full_name=full_name)
            logger.info(f"New user authorized: {user_id}")
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")

    def set_user_profile(
        self,
        user_id: int,
        full_name: str | None = None,
        role: str | None = None,
        department: str | None = None,
    ):
        normalized_role = (role or "").strip().lower() or None
        if normalized_role is None and user_id in self.user_profiles:
            normalized_role = self.user_profiles[user_id].get("role") or "employee"
        if normalized_role is None:
            normalized_role = "employee"

        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)", (int(user_id),))
                conn.execute(
                    "INSERT INTO user_profiles(user_id, full_name, role, department) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "full_name = COALESCE(excluded.full_name, user_profiles.full_name), "
                    "role = COALESCE(excluded.role, user_profiles.role), "
                    "department = COALESCE(excluded.department, user_profiles.department), "
                    "updated_at = CURRENT_TIMESTAMP",
                    (int(user_id), full_name, normalized_role, department),
                )
                conn.commit()

            self.users.add(user_id)
            current = self.user_profiles.get(user_id, {"user_id": user_id})
            current["full_name"] = full_name or current.get("full_name")
            current["role"] = normalized_role or current.get("role") or "employee"
            current["department"] = department or current.get("department")
            self.user_profiles[user_id] = current

            self._supabase.upsert_work_user(
                user_id=user_id,
                full_name=current.get("full_name"),
                role=current.get("role"),
                department=current.get("department"),
            )
        except Exception as e:
            logger.error(f"Error setting profile for user {user_id}: {e}")

    def get_user_profile(self, user_id: int) -> dict:
        profile = self.user_profiles.get(user_id)
        if profile:
            return profile

        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT user_id, full_name, role, department FROM user_profiles WHERE user_id = ?",
                    (int(user_id),),
                ).fetchone()
            if row:
                profile = {
                    "user_id": row[0],
                    "full_name": row[1],
                    "role": row[2] or "employee",
                    "department": row[3],
                }
                self.user_profiles[user_id] = profile
                return profile
        except Exception as e:
            logger.error(f"Error getting profile for user {user_id}: {e}")

        return {
            "user_id": int(user_id),
            "full_name": None,
            "role": "employee",
            "department": None,
        }

    def list_user_profiles(self) -> list[dict]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT user_id, full_name, role, department FROM user_profiles ORDER BY updated_at DESC"
                ).fetchall()
            result = [
                {
                    "user_id": row[0],
                    "full_name": row[1],
                    "role": row[2] or "employee",
                    "department": row[3],
                }
                for row in rows
            ]
            for item in result:
                self.user_profiles[item["user_id"]] = item
            return result
        except Exception as e:
            logger.error(f"Error listing user profiles: {e}")
            return []

    def add_chat(self, chat_id: int):
        """Add a chat to the known work chats list and persist to DB."""
        if chat_id in self.chats:
            return

        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR IGNORE INTO allowed_chats (chat_id) VALUES (?)", (int(chat_id),))
                conn.commit()
            self.chats.add(chat_id)
            self._supabase.upsert_work_chat(chat_id)
            logger.info(f"New work chat authorized: {chat_id}")
        except Exception as e:
            logger.error(f"Error adding chat {chat_id}: {e}")

    def is_user_known(self, user_id: int) -> bool:
        """Check if user is already known/cached."""
        return user_id in self.users

    def get_known_chats(self) -> Set[int]:
        """Return list of known work chats."""
        return self.chats

    def set_active_chat(self, user_id: int, chat_id: int):
        """Remember the latest work chat for this user to continue context in PM."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO active_user_chats(user_id, chat_id) VALUES(?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, updated_at=CURRENT_TIMESTAMP",
                    (int(user_id), int(chat_id)),
                )
                conn.commit()
            self.active_chat_by_user[user_id] = chat_id
            self._supabase.upsert_work_user(user_id=user_id)
            self._supabase.upsert_work_chat(chat_id)
            self._supabase.upsert_active_user_chat(user_id, chat_id)
        except Exception as e:
            logger.error(f"Error setting active chat {chat_id} for user {user_id}: {e}")

    def get_active_chat(self, user_id: int) -> int | None:
        return self.active_chat_by_user.get(user_id)

    def get_diagnostics(self) -> dict:
        return {
            "db_path": self.DB_PATH,
            "db_exists": os.path.exists(self.DB_PATH),
            "legacy_file_exists": os.path.exists(self.LEGACY_FILE_PATH),
            "known_users": len(self.users),
            "known_chats": len(self.chats),
            "active_context_links": len(self.active_chat_by_user),
            "profiles_count": len(self.user_profiles),
        }
