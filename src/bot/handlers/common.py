from aiogram import Router, types
from aiogram.filters import Command, CommandStart  # Keep explicit imports for clarity
from bot.filters import IsAllowedUser

router = Router()

@router.message(CommandStart(), ~IsAllowedUser())
async def command_start_unauthorized(message: types.Message) -> None:
    """
    Handler for unauthorized users in PM.
    """
    await message.answer(
        f"⛔️ Я пока не знаком с вами.\n"
        f"Чтобы я начал работать в личных сообщениях, пожалуйста, <b>напишите любое сообщение в любой рабочий чат</b>, где я добавлен.\n"
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>"
    )

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
