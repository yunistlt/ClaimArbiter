from typing import Dict, Optional, List
from models import IncidentCard, ChatMessage
import json
import logging
import os
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)

class IncidentManager:
    """
    Manages the state of incident cards for each chat.
    Uses a simple JSON file for persistence across restarts.
    """
    _incidents: Dict[int, IncidentCard] = {}
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    DATA_DIR = os.path.join(BASE_DIR, "data")
    LEGACY_STORAGE_FILE = os.path.join(BASE_DIR, "incidents.json")
    _storage_file: str = os.getenv("INCIDENTS_STORAGE_PATH", os.path.join(DATA_DIR, "incidents.json"))
    _loaded: bool = False

    @classmethod
    def _ensure_storage_ready(cls):
        os.makedirs(os.path.dirname(cls._storage_file), exist_ok=True)

    @classmethod
    def _migrate_legacy_storage_if_needed(cls):
        if cls._storage_file == cls.LEGACY_STORAGE_FILE:
            return
        if os.path.exists(cls._storage_file) or not os.path.exists(cls.LEGACY_STORAGE_FILE):
            return

        try:
            os.replace(cls.LEGACY_STORAGE_FILE, cls._storage_file)
            logger.info("Migrated incidents storage to persistent data directory.")
        except Exception as e:
            logger.error(f"Error migrating incidents storage: {e}")

    @classmethod
    def load_from_disk(cls):
        """Loads incidents from the JSON file."""
        cls._ensure_storage_ready()
        cls._migrate_legacy_storage_if_needed()
        if not os.path.exists(cls._storage_file):
            return
            
        try:
            with open(cls._storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Convert loaded JSON dict back to IncidentCard objects
                for chat_id_str, card_data in data.items():
                    try:
                        chat_id = int(chat_id_str)
                        card = IncidentCard.model_validate(card_data)
                        cls._incidents[chat_id] = card
                    except Exception as e:
                       logger.error(f"Failed to load incident for chat {chat_id_str}: {e}")
            logger.info(f"Loaded {len(cls._incidents)} incidents from disk.")
        except Exception as e:
            logger.error(f"Error loading incidents from disk: {e}")
            
    @classmethod
    def save_to_disk(cls):
        """Saves current state to JSON file."""
        try:
            cls._ensure_storage_ready()
            data = {}
            for chat_id, card in cls._incidents.items():
                data[str(chat_id)] = card.model_dump(mode='json')
                
            with open(cls._storage_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving incidents to disk: {e}")

    @classmethod
    def _ensure_loaded(cls):
        if not cls._loaded:
            cls.load_from_disk()
            cls._loaded = True

    @classmethod
    def get_or_create_incident(cls, chat_id: int) -> IncidentCard:
        cls._ensure_loaded()
        if chat_id not in cls._incidents:
            cls._incidents[chat_id] = IncidentCard(chat_id=chat_id)
            cls.save_to_disk()
        return cls._incidents[chat_id]

    @classmethod
    def get_incident(cls, chat_id: int) -> Optional[IncidentCard]:
        cls._ensure_loaded()
        return cls._incidents.get(chat_id)

    @classmethod
    def update_incident(cls, chat_id: int, card: IncidentCard):
        cls._ensure_loaded()
        cls._incidents[chat_id] = card
        cls.save_to_disk()
        
    @classmethod
    def add_message(cls, chat_id: int, role: str, content: str, username: Optional[str] = None):
        card = cls.get_or_create_incident(chat_id)
        msg = ChatMessage(role=role, content=content, username=username)
        # Keep recent history (increased limit for context)
        card.chat_history.append(msg)
        if len(card.chat_history) > 50:
             card.chat_history = card.chat_history[-50:]
        cls.update_incident(chat_id, card)

    @classmethod
    def get_diagnostics(cls) -> dict:
        cls._ensure_loaded()
        cls._ensure_storage_ready()
        return {
            "storage_path": cls._storage_file,
            "storage_exists": os.path.exists(cls._storage_file),
            "legacy_storage_exists": os.path.exists(cls.LEGACY_STORAGE_FILE),
            "incidents_count": len(cls._incidents),
            "messages_count": sum(len(card.chat_history) for card in cls._incidents.values()),
        }
