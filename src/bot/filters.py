from aiogram.filters import BaseFilter
from aiogram.types import Message
import logging
from config import ALLOWED_USER_IDS
from services.access_control import AccessControl

class IsAllowedUser(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        """
        Правила доступа (Динамические):
        1. Если сообщение из ГРУППЫ -> Сохраняем ID чата и ID пользователя. Разрешаем.
        2. Если сообщение в ЛИЧКУ ->
           - Проверяем кеш пользователей (AccessControl). Если есть -> Разрешаем.
           - Если нет, бот "прозванивает" известные рабочие чаты (get_chat_member).
             Если пользователь найден -> Добавляем в кеш, разрешаем.
             Если нигде не найден -> Запрещаем.
        """
        user_id = message.from_user.id
        access_control = AccessControl()
        bot = message.bot
        
        # 1. Поведение в групповых чатах: Авторизация чата и пользователя
        if message.chat.type in ["group", "supergroup"]:
            # Запоминаем этот чат как рабочий
            access_control.add_chat(message.chat.id)
            # Запоминаем пользователя (на всякий случай, чтобы быстрее отвечать в ЛС)
            access_control.add_user(user_id)
            return True
        
        # 2. Поведение в личке: Проверка
        
        # а) Кеш и админы
        if ALLOWED_USER_IDS and user_id in ALLOWED_USER_IDS:
             return True
        if access_control.is_user_known(user_id):
            return True

        # б) Если пользователя нет в кеше, ищем его в известных чатах
        known_chats = access_control.get_known_chats()
        if not known_chats:
            # Бот еще не добавлен ни в один чат, или база пуста.
            logging.warning(f"Unknown user {user_id} in PM, and no known chats to check.")
            return False

        for chat_id in known_chats:
            try:
                member_status = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                # Статусы, которые считаются "своими": creator, administrator, member, restricted (но не left/kicked)
                if member_status.status in ["creator", "administrator", "member", "restricted"]:
                    logging.info(f"User {user_id} found in chat {chat_id}. Authorizing.")
                    access_control.add_user(user_id) # Запоминаем навсегда
                    return True
            except Exception as e:
                # Возможно, бота удалили из этого чата или другая ошибка
                logging.warning(f"Could not check membership in {chat_id}: {e}")
                continue

        # Если прошли все чаты и не нашли
        logging.warning(f"Unauthorized PM access attempt: {user_id}. Not found in any known chats.")
        return False
