import logging
from typing import Any, Dict, Optional

from config import SUPABASE_ENABLED, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)


class SupabaseStorage:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SupabaseStorage, cls).__new__(cls)
            cls._instance._client = None
            cls._instance._enabled = False
            cls._instance._init_client()
        return cls._instance

    def _init_client(self):
        if not SUPABASE_ENABLED:
            logger.info("Supabase is disabled. Using local storage only.")
            return

        try:
            from supabase import create_client

            self._client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            self._enabled = True
            logger.info("Supabase storage enabled.")
        except Exception as e:
            logger.error(f"Could not initialize Supabase client: {e}")
            self._client = None
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def _upsert(self, table: str, payload: Dict[str, Any], on_conflict: Optional[str] = None):
        if not self.enabled:
            return
        try:
            if on_conflict:
                self._client.table(table).upsert(payload, on_conflict=on_conflict).execute()
            else:
                self._client.table(table).upsert(payload).execute()
        except Exception as e:
            logger.warning(f"Supabase upsert failed for {table}: {e}")

    def _insert(self, table: str, payload: Dict[str, Any]):
        if not self.enabled:
            return
        try:
            self._client.table(table).insert(payload).execute()
        except Exception as e:
            logger.warning(f"Supabase insert failed for {table}: {e}")

    def _update(self, table: str, values: Dict[str, Any], filters: Dict[str, Any]):
        if not self.enabled:
            return
        try:
            query = self._client.table(table).update(values)
            for key, value in filters.items():
                query = query.eq(key, value)
            query.execute()
        except Exception as e:
            logger.warning(f"Supabase update failed for {table}: {e}")

    def upsert_work_chat(self, chat_id: int):
        self._upsert(
            "work_chats",
            {
                "tg_chat_id": int(chat_id),
            },
            on_conflict="tg_chat_id",
        )

    def upsert_work_user(self, user_id: int):
        self._upsert(
            "work_users",
            {
                "tg_user_id": int(user_id),
            },
            on_conflict="tg_user_id",
        )

    def upsert_active_user_chat(self, user_id: int, chat_id: int):
        self._upsert(
            "active_user_chats",
            {
                "tg_user_id": int(user_id),
                "tg_chat_id": int(chat_id),
            },
            on_conflict="tg_user_id",
        )

    def upsert_legal_case(self, card_payload: Dict[str, Any]):
        chat_id = card_payload.get("chat_id")
        if chat_id is None:
            return

        payload = {
            "tg_chat_id": int(chat_id),
            "task_type": card_payload.get("task_type") or "consultation",
            "status": card_payload.get("status") or "init",
            "task_description": card_payload.get("task_description"),
            "current_stage": card_payload.get("current_stage"),
            "known_facts": card_payload.get("known_facts"),
            "missing_info": card_payload.get("missing_info"),
            "next_step": card_payload.get("next_step"),
            "eta_text": card_payload.get("eta_text"),
            "key_risks": card_payload.get("key_risks"),
            "technical_verdict": card_payload.get("technical_verdict"),
            "legal_strategy": card_payload.get("legal_strategy"),
            "generated_response": card_payload.get("generated_response"),
        }
        self._upsert("legal_cases", payload, on_conflict="tg_chat_id")

    def insert_work_message(self, chat_id: int, role: str, content: str, username: Optional[str] = None):
        payload = {
            "tg_chat_id": int(chat_id),
            "role": role,
            "username": username,
            "content": content,
        }
        self._insert("work_messages", payload)

    def upsert_review_rule(self, task_type: str, mode: str):
        self._upsert(
            "review_rules",
            {
                "task_type": task_type,
                "mode": mode,
            },
            on_conflict="task_type",
        )

    def insert_review_task(
        self,
        chat_id: int,
        requester_user_id: int,
        requester_name: str,
        task_type: str,
        content: str,
    ):
        payload = {
            "tg_chat_id": int(chat_id),
            "requester_tg_user_id": int(requester_user_id),
            "requester_name": requester_name,
            "task_type": task_type,
            "content": content,
            "status": "pending",
        }
        self._insert("review_tasks", payload)

    def update_review_task_status_by_content(
        self,
        chat_id: int,
        requester_user_id: int,
        content: str,
        status: str,
        reviewer_id: Optional[int] = None,
        reviewer_comment: Optional[str] = None,
    ):
        values = {
            "status": status,
            "reviewer_tg_user_id": reviewer_id,
            "reviewer_comment": reviewer_comment,
        }
        filters = {
            "tg_chat_id": int(chat_id),
            "requester_tg_user_id": int(requester_user_id),
            "content": content,
        }
        self._update("review_tasks", values, filters)
