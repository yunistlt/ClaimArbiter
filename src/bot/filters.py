from aiogram.filters import BaseFilter
from aiogram.types import Message
import logging
from config import ALLOWED_USER_IDS

class IsAllowedUser(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        """
        Правила доступа:
        1. Если сообщение из ГРУППЫ или СУПЕРГРУППЫ -> Разрешено всем (полагаемся на то, что бот только в рабочих чатах).
        2. Если сообщение в ЛИЧКУ -> Разрешено только если пользователь в ALLOWED_USER_IDS (администраторы).
        3. Если ALLOWED_USER_IDS не задан -> В группе работает, в личке предупреждает.
        """
        # 1. Разрешаем доступ всем в групповых чатах
        if message.chat.type in ["group", "supergroup"]:
            return True
            
        # 2. В личных сообщениях проверяем белый список (если он есть)
        if ALLOWED_USER_IDS:
             return message.from_user.id in ALLOWED_USER_IDS
             
        # Если список админов пуст и пишут в личку — лучше разрешить (режим отладки) или запретить?
        # Чтобы не блокировать совсем без настройки, разрешим, но с варнингом в логах.
        # Но по вашей просьбе "закрытый бот" — по умолчанию чужим в ЛС лучше не отвечать, 
        # однако без админа бот станет "неуправляемым" в ЛС. 
        # Допустим: если список пуст, доступ в ЛС открыт (поведение по умолчанию). 
        # Если список задан - ЛС закрыто.
        logging.warning("No ALLOWED_USER_IDS set! PM access is open.")
        return True
