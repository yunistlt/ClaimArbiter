import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ReviewTask:
    id: int
    chat_id: int
    requester_user_id: int
    requester_name: str
    task_type: str
    content: str
    status: str
    reviewer_id: Optional[int]
    reviewer_comment: Optional[str]


class ReviewQueue:
    _instance = None

    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    DB_PATH = os.getenv("REVIEW_QUEUE_DB_PATH", os.path.join(DATA_DIR, "review_queue.db"))

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ReviewQueue, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _conn(self):
        return sqlite3.connect(self.DB_PATH)

    def _init_db(self):
        os.makedirs(os.path.dirname(self.DB_PATH), exist_ok=True)
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS review_rules (
                    task_type TEXT PRIMARY KEY,
                    mode TEXT NOT NULL CHECK(mode IN ('auto', 'manual'))
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS review_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    requester_user_id INTEGER NOT NULL,
                    requester_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
                    reviewer_id INTEGER,
                    reviewer_comment TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

        # Conservative defaults: drafts/claims go through review, consultations auto-release.
        for task_type, mode in [
            ("claim_processing", "manual"),
            ("claim", "manual"),
            ("document_drafting", "manual"),
            ("legal_advice", "auto"),
            ("consultation", "auto"),
        ]:
            self.set_rule(task_type, mode)

    def set_rule(self, task_type: str, mode: str):
        if mode not in ["auto", "manual"]:
            raise ValueError("mode must be 'auto' or 'manual'")

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO review_rules(task_type, mode) VALUES(?, ?) "
                "ON CONFLICT(task_type) DO UPDATE SET mode=excluded.mode",
                (task_type, mode),
            )
            conn.commit()

    def get_rule(self, task_type: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT mode FROM review_rules WHERE task_type = ?", (task_type,)).fetchone()
            if row:
                return row[0]
        return "manual"

    def list_rules(self) -> List[tuple]:
        with self._conn() as conn:
            rows = conn.execute("SELECT task_type, mode FROM review_rules ORDER BY task_type").fetchall()
        return [(row[0], row[1]) for row in rows]

    def enqueue(self, chat_id: int, requester_user_id: int, requester_name: str, task_type: str, content: str) -> int:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO review_tasks(chat_id, requester_user_id, requester_name, task_type, content)
                VALUES(?, ?, ?, ?, ?)
                """,
                (chat_id, requester_user_id, requester_name, task_type, content),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_pending(self, limit: int = 20) -> List[ReviewTask]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, chat_id, requester_user_id, requester_name, task_type, content, status, reviewer_id, reviewer_comment
                FROM review_tasks
                WHERE status = 'pending'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [ReviewTask(*row) for row in rows]

    def get_task(self, task_id: int) -> Optional[ReviewTask]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id, chat_id, requester_user_id, requester_name, task_type, content, status, reviewer_id, reviewer_comment
                FROM review_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        return ReviewTask(*row) if row else None

    def approve(self, task_id: int, reviewer_id: int):
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE review_tasks
                SET status = 'approved', reviewer_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (reviewer_id, task_id),
            )
            conn.commit()

    def reject(self, task_id: int, reviewer_id: int, comment: str):
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE review_tasks
                SET status = 'rejected', reviewer_id = ?, reviewer_comment = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (reviewer_id, comment, task_id),
            )
            conn.commit()

    def get_diagnostics(self) -> dict:
        with self._conn() as conn:
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM review_tasks WHERE status = 'pending'"
            ).fetchone()[0]
            rules_count = conn.execute("SELECT COUNT(*) FROM review_rules").fetchone()[0]

        return {
            "db_path": self.DB_PATH,
            "db_exists": os.path.exists(self.DB_PATH),
            "pending_tasks": pending_count,
            "rules_count": rules_count,
        }
