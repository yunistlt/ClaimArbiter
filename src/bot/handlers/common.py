from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart  # Keep explicit imports for clarity
from bot.filters import IsAllowedUser
from services.access_control import AccessControl

router = Router()
access_control = AccessControl()

@router.message(F.content_type.in_({'new_chat_members', 'left_chat_member', 'group_chat_created', 'supergroup_chat_created'}))
async def on_user_joined(message: types.Message):
    """
    Триггер на любое системное событие (кто-то вступил, бот добавлен).
    Гарантированно запоминаем чат и всех участников.
    """
    if message.chat.type in ["group", "supergroup"]:
        access_control = AccessControl()
        access_control.add_chat(message.chat.id)
        if message.from_user and not message.from_user.is_bot:
             access_control.add_user(message.from_user.id)
             
        # Если добавили самого бота
        bot_user = await message.bot.get_me()
        if message.new_chat_members:
            for member in message.new_chat_members:
                if member.id == bot_user.id:
                    await message.answer(
                        "👨‍⚖️ <b>Я подключен!</b>\n\n"
                        "✅ Я сохранил ID этого чата.\n"
                        "✅ Теперь все участники могут писать мне в личку (ЛС).\n"
                        "⚠️ <b>Важно:</b> Сделайте меня <b>Администратором</b>, чтобы я видел сообщения всех сотрудников."
                    )
                elif not member.is_bot:
                    access_control.add_user(member.id)

@router.message(CommandStart(), ~IsAllowedUser())
async def command_start_unauthorized(message: types.Message) -> None:
async def command_start_unauthorized(message: types.Message) -> None:
    """
    Handler for unauthorized users in PM.
    """
    await message.answer(
        f"⛔️ Я пока не знаком с вами.\n"
        f"Чтобы я начал работать в личных сообщениях, пожалуйста, <b>напишите любое сообщение в любой рабочий чат</b>, где я добавлен.\n"
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>"
    )

@router.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def process_any_group_message(message: types.Message):
    """
    Пассивный слушатель всех сообщений в группах.
    Нужен, чтобы бот "увидел" чат и запомнил участников.
    """
    # Гарантированно запоминаем чат и автора сообщения
    access_control.add_chat(message.chat.id)
    if not message.from_user.is_bot:
        access_control.add_user(message.from_user.id)
    # Далее управление передается другим хендлерам (через продолжение?) 
    # Нет, в aiogram 3.x Message Handler'ы терминальны по умолчанию, если не middleware. 
    # Но так как этот хендлер стоит ВЫШЕ всех остальных текстовых в common, он может перехватить.
    # Поэтому мы НЕ ставим его здесь как терминальный, или используем middleware.
    # В данном случае, лучше встроить эту логику в IsAllowedUser фильтр, что уже сделано.
    # Но если фильтр не сработал (например, для системных сообщений?), то эта страховка.
    pass 

@router.message(CommandStart(), IsAllowedUser())
async def command_start_handler(message: types.Message) -> None:
    """ 
    This handler receives messages with `/start` command from allowed users
    """
    await message.answer(f"Привет, {message.from_user.full_name}! Я бот 'ЗМК-Юрист'.\n"
                         f"Я помогу с разбором рекламаций. Напиши /status для проверки текущей ситуации или /help для справки.")

@router.message(Command("status"), IsAllowedUser())
async def command_status_handler(message: types.Message) -> None:
    """
    Handler for /status command
    """
    await message.answer("Статус: Ожидание документов.\n(Заглушка для проверки работы)")

@router.message(Command("help"), IsAllowedUser())
async def command_help_handler(message: types.Message) -> None:
    """
    Handler for /help command
    """
    await message.answer("Доступные команды:\n"
                         "/start - Начало работы\n"
                         "/status - Правовая оценка ситуации\n"
                         "/write - Генерация документа (в разработке)\n"
                         "/files - Список документов (в разработке)")
