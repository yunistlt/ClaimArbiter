from aiogram.filters import BaseFilter
from aiogram.types import Message
import logging
from config import ALLOWED_USER_IDS

class IsAllowedUser(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        """
        Check if user is in ALLOWED_USER_IDS list.
        If list is empty, logs warning and allows access (dev mode).
        If user is not in list, denies access.
        """
        if not ALLOWED_USER_IDS:
            logging.warning("No ALLOWED_USER_IDS set! Bot is public.")
            return True
            
        return message.from_user.id in ALLOWED_USER_IDS
