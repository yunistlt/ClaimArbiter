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
            cls._instance.load_users()
        return cls._instance

    def load_users(self):
        """Load allowed users from JSON file."""
        if not os.path.exists(self.FILE_PATH):
            self.users = set()
            return

        try:
            with open(self.FILE_PATH, 'r') as f:
                data = json.load(f)
                self.users = set(data.get("allowed_ids", []))
            logger.info(f"Loaded {len(self.users)} allowed users.")
        except Exception as e:
            logger.error(f"Error loading allowed users: {e}")
            self.users = set()

    def save_users(self):
        """Save current users to JSON file."""
        try:
            with open(self.FILE_PATH, 'w') as f:
                json.dump({"allowed_ids": list(self.users)}, f)
        except Exception as e:
            logger.error(f"Error saving allowed users: {e}")

    def add_user(self, user_id: int):
        """Add a user to the allowlist if not already present."""
        if user_id not in self.users:
            self.users.add(user_id)
            self.save_users()
            logger.info(f"New user authorized from group chat: {user_id}")

    def is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed."""
        return user_id in self.users
