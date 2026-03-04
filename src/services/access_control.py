import json
import logging
import os
from typing import Set

logger = logging.getLogger(__name__)

class AccessControl:
    _instance = None
    FILE_PATH = "allowed_users.json"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AccessControl, cls).__new__(cls)
            cls._instance.users = set()
            cls._instance.chats = set()
            cls._instance.load_data()
        return cls._instance

    def load_data(self):
        """Load allowed users and chats from JSON file."""
        if not os.path.exists(self.FILE_PATH):
            self.users = set()
            self.chats = set()
            return

        try:
            with open(self.FILE_PATH, 'r') as f:
                data = json.load(f)
                self.users = set(data.get("allowed_ids", []))
                self.chats = set(data.get("allowed_chats", []))
            logger.info(f"Loaded {len(self.users)} allowed users and {len(self.chats)} chats.")
        except Exception as e:
            logger.error(f"Error loading access data: {e}")
            self.users = set()
            self.chats = set()

    def save_data(self):
        """Save current users and chats to JSON file."""
        try:
            with open(self.FILE_PATH, 'w') as f:
                json.dump({
                    "allowed_ids": list(self.users),
                    "allowed_chats": list(self.chats)
                }, f)
        except Exception as e:
            logger.error(f"Error saving access data: {e}")

    def add_user(self, user_id: int):
        """Add a user to the allowlist."""
        if user_id not in self.users:
            self.users.add(user_id)
            self.save_data()
            logger.info(f"New user authorized: {user_id}")

    def add_chat(self, chat_id: int):
        """Add a chat to the known work chats list."""
        if chat_id not in self.chats:
            self.chats.add(chat_id)
            self.save_data()
            logger.info(f"New work chat authorized: {chat_id}")

    def is_user_known(self, user_id: int) -> bool:
        """Check if user is already known/cached."""
        return user_id in self.users

    def get_known_chats(self) -> Set[int]:
        """Return list of known work chats."""
        return self.chats
