from aiogram import Router, types, F
from aiogram.filters import Command, CommandStart  # Keep explicit imports for clarity
from bot.filters import IsAllowedUser
from services.access_control import AccessControl
from services.incident_manager import IncidentManager
from services.review_queue import ReviewQueue
from config import AUTO_ALLOW_PRIVATE_USERS, LAWYER_REVIEWER_IDS

router = Router()
access_control = AccessControl()
review_queue = ReviewQueue()


def can_manage_roles(user_id: int) -> bool:
    return user_id in LAWYER_REVIEWER_IDS


def unauthorized_private_text(user_id: int) -> str:
    return (
        "⛔️ Я пока не знаком с вами.\n"
        "Чтобы я начал работать в личных сообщениях, пожалуйста, <b>напишите любое сообщение в любой рабочий чат</b>, где я добавлен.\n"
        f"Ваш Telegram ID: <code>{user_id}</code>"
    )

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
               access_control.add_user(message.from_user.id, full_name=message.from_user.full_name)
             
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
                    access_control.add_user(member.id, full_name=member.full_name)

@router.message(CommandStart(), ~IsAllowedUser())
async def command_start_unauthorized(message: types.Message) -> None:
    """
    Handler for unauthorized users in PM.
    """
    if AUTO_ALLOW_PRIVATE_USERS and message.chat.type == "private" and message.from_user:
        access_control.add_user(message.from_user.id, full_name=message.from_user.full_name)
        await message.answer(
            f"✅ Доступ в личке активирован, {message.from_user.full_name}.\n"
            "Теперь можно писать запрос в свободной форме."
        )
        return

    await message.answer(unauthorized_private_text(message.from_user.id))


@router.message(F.chat.type == "private", ~F.text.startswith('/'), ~IsAllowedUser())
async def private_message_unauthorized(message: types.Message) -> None:
    """
    Prevent silent drops for unauthorized users in private chats.
    """
    if AUTO_ALLOW_PRIVATE_USERS and message.from_user:
        access_control.add_user(message.from_user.id, full_name=message.from_user.full_name)
        await message.answer("✅ Доступ в личке активирован. Повторите, пожалуйста, ваш запрос.")
        return

    await message.answer(unauthorized_private_text(message.from_user.id))

@router.message(CommandStart(), IsAllowedUser())
async def command_start_handler(message: types.Message) -> None:
    """ 
    This handler receives messages with `/start` command from allowed users
    """
    await message.answer(f"Привет, {message.from_user.full_name}! Я бот 'ЗМК-Юрист'.\n"
                         f"Я помогаю по всем рабочим юридическим вопросам компании. Напиши /status для проверки текущей ситуации или /help для справки.")

@router.message(Command("status"), IsAllowedUser())
async def command_status_handler(message: types.Message) -> None:
    """
    Handler for /status command
    """
    await message.answer("Статус: Готов к обработке юридической задачи.\n(Заглушка для проверки работы)")

@router.message(Command("help"), IsAllowedUser())
async def command_help_handler(message: types.Message) -> None:
    """
    Handler for /help command
    """
    await message.answer("Доступные команды:\n"
                         "/start - Начало работы\n"
                         "/status - Правовая оценка ситуации\n"
                         "/diag - Диагностика состояния памяти и хранилищ\n"
                         "/whoami - Мой профиль и роль\n"
                         "/write - Генерация документа (в разработке)\n"
                         "/files - Список документов (в разработке)\n\n"
                         "Команды юриста (в личном чате):\n"
                         "/setrole <user_id> <role> [department] - Назначить роль\n"
                         "/review_rules - Показать режимы согласования\n"
                         "/review_set <task_type> <auto|manual> - Изменить режим\n"
                         "/review_queue - Очередь на проверку\n"
                         "/review_approve <id> - Согласовать и отправить\n"
                         "/review_reject <id> <причина> - Отклонить")


@router.message(Command("diag"), IsAllowedUser())
async def command_diag_handler(message: types.Message) -> None:
    incident_diag = IncidentManager.get_diagnostics()
    access_diag = access_control.get_diagnostics()
    review_diag = review_queue.get_diagnostics()

    text = (
        "🧪 <b>Диагностика состояния</b>\n\n"
        f"Инциденты: <b>{incident_diag['incidents_count']}</b>\n"
        f"Сообщения в памяти: <b>{incident_diag['messages_count']}</b>\n"
        f"Файл инцидентов: <code>{incident_diag['storage_path']}</code>\n"
        f"Файл инцидентов существует: <b>{'да' if incident_diag['storage_exists'] else 'нет'}</b>\n"
        f"Legacy incidents.json остался: <b>{'да' if incident_diag['legacy_storage_exists'] else 'нет'}</b>\n\n"
        f"База доступа: <code>{access_diag['db_path']}</code>\n"
        f"База доступа существует: <b>{'да' if access_diag['db_exists'] else 'нет'}</b>\n"
        f"Известных пользователей: <b>{access_diag['known_users']}</b>\n"
        f"Профилей сотрудников: <b>{access_diag['profiles_count']}</b>\n"
        f"Известных чатов: <b>{access_diag['known_chats']}</b>\n"
        f"Связок пользователь→чат: <b>{access_diag['active_context_links']}</b>\n"
        f"Legacy allowed_users.json остался: <b>{'да' if access_diag['legacy_file_exists'] else 'нет'}</b>\n\n"
        f"База очереди: <code>{review_diag['db_path']}</code>\n"
        f"База очереди существует: <b>{'да' if review_diag['db_exists'] else 'нет'}</b>\n"
        f"Правил согласования: <b>{review_diag['rules_count']}</b>\n"
        f"Задач в очереди: <b>{review_diag['pending_tasks']}</b>"
    )
    await message.answer(text)


@router.message(Command("whoami"), IsAllowedUser())
async def command_whoami_handler(message: types.Message) -> None:
    profile = access_control.get_user_profile(message.from_user.id)
    role = profile.get("role") or "employee"
    department = profile.get("department") or "не указан"
    full_name = profile.get("full_name") or message.from_user.full_name

    await message.answer(
        "👤 <b>Ваш профиль</b>\n\n"
        f"ID: <code>{message.from_user.id}</code>\n"
        f"ФИО: <b>{full_name}</b>\n"
        f"Роль: <b>{role}</b>\n"
        f"Отдел: <b>{department}</b>"
    )


@router.message(Command("setrole"), IsAllowedUser())
async def command_setrole_handler(message: types.Message) -> None:
    if not can_manage_roles(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав для назначения ролей.")
        return

    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Формат: /setrole <user_id> <role> [department]")
        return

    user_id_raw = parts[1].strip()
    role = parts[2].strip().lower()
    department = parts[3].strip() if len(parts) > 3 else None

    if not user_id_raw.isdigit():
        await message.answer("user_id должен быть числом.")
        return

    user_id = int(user_id_raw)
    full_name = None
    existing = access_control.get_user_profile(user_id)
    if existing:
        full_name = existing.get("full_name")

    access_control.set_user_profile(
        user_id=user_id,
        full_name=full_name,
        role=role,
        department=department,
    )

    updated = access_control.get_user_profile(user_id)
    await message.answer(
        "✅ <b>Профиль обновлен</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Роль: <b>{updated.get('role') or role}</b>\n"
        f"Отдел: <b>{updated.get('department') or (department or 'не указан')}</b>"
    )
