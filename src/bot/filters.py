from aiogram.filters import BaseFilter
from aiogram.types import Message
import logging
from config import ALLOWED_USER_IDS
from services.access_control import AccessControl

class IsAllowedUser(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        """
        Правила доступа (Динамические):
        1. Если сообщение из ГРУППЫ -> Бот сохраняет ID участника в локальный список (авторизует).
        2. Если сообщение в ЛИЧКУ -> Бот проверяет локальный список.
           Если пользователя нет в списке (он ни разу не писал в группе), доступ закрыт.
        """
        user_id = message.from_user.id
        access_control = AccessControl()
        
        # 1. Поведение в групповых чатах: Автоматическая авторизация
        if message.chat.type in ["group", "supergroup"]:
            # Если пользователь пишет в рабочий чат, значит он сотрудник. Добавляем его.
            access_control.add_user(user_id)
            return True
        
        # 2. Поведение в личке: Проверка
        # Сначала проверяем жестко заданных админов из конфига (на всякий случай)
        if ALLOWED_USER_IDS and user_id in ALLOWED_USER_IDS:
             return True
             
        # Затем проверяем тех, кто был замечен в группах
        if access_control.is_allowed(user_id):
            return True

        # Если нигде не нашли
        logging.warning(f"Unauthorized PM access attempt: {user_id}")
        return False
