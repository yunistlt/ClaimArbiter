from aiogram import Router, F, types
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from utils.llm import get_llm
from agents.secretary import SecretaryAgent
from agents.engineer import EngineerAgent
from agents.lawyer import LawyerAgent
from agents.clerk import ClerkAgent
from models import IncidentCard, DocumentInfo
from services.incident_manager import IncidentManager
from services.pdf_service import create_pdf
from services.review_queue import ReviewQueue
from services.access_control import AccessControl
from aiogram.types import FSInputFile
from bot.filters import IsAllowedUser
from aiogram.filters import Command
from config import LAWYER_REVIEWER_IDS
import asyncio
import json
import os
import tempfile
import logging
import re
from html import escape

router = Router()
secretary = SecretaryAgent()
engineer = EngineerAgent()
lawyer = LawyerAgent()
clerk = ClerkAgent()
review_queue = ReviewQueue()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from langchain_core.tools import tool

# Define tool at module level to avoid redefinition/pickling issues
@tool
def delegate_task(task_type: str, description: str):
    """
    Call this tool when the user asks for a specific legal service that requires formal processing.
    
    Args:
        task_type (str): The type of task. Must be one of:
            - 'claim_processing': For product defects, returns, warranties (involves Engineer).
            - 'document_drafting': For creating contracts, claims, letters, agreements, lawsuits (no Engineer needed).
            - 'legal_advice': For complex legal questions requiring formal analysis or due diligence.
            - 'consultation': For discussion mode, clarification and recommendations without formal document output.
        description (str): A summary of what needs to be done.
    """
    return "TASK_DELEGATED"


def human_task_type(task_type: str) -> str:
    mapping = {
        "claim_processing": "претензионная работа",
        "claim": "претензионная работа",
        "document_drafting": "подготовка документа",
        "legal_advice": "юридическая консультация",
        "consultation": "юридическая консультация",
    }
    return mapping.get(task_type, "юридическая задача")


def is_reviewer(user_id: int) -> bool:
    return user_id in LAWYER_REVIEWER_IDS


async def send_pdf_to_chat(message: types.Message, chat_id: int, text: str, caption_prefix: str):
    pdf_filename = f"ZMK_Doc_{chat_id}_{message.message_id}.pdf"
    temp_dir = tempfile.gettempdir()
    full_pdf_path = os.path.join(temp_dir, pdf_filename)

    try:
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, create_pdf, text, full_pdf_path)
        if success and os.path.exists(full_pdf_path):
            file_to_send = FSInputFile(full_pdf_path)
            await message.bot.send_document(
                chat_id=chat_id,
                document=file_to_send,
                caption=f"📄 <b>Документ (PDF)</b>\n{caption_prefix}",
            )
            os.remove(full_pdf_path)
            return True
    except Exception as e:
        logger.error(f"Error generating/sending PDF: {e}")

    return False


def is_bot_mentioned_in_entities(text: str, entities, bot_username: str, bot_id: int) -> bool:
    if not entities:
        return False

    for entity in entities:
        if entity.type == "mention":
            mention_text = text[entity.offset:entity.offset + entity.length]
            if f"@{bot_username}".lower() == mention_text.lower():
                return True
        elif entity.type == "text_mention" and entity.user and entity.user.id == bot_id:
            return True

    return False


def resolve_context_chat_id(message: types.Message) -> int:
    """
    In PM, continue working with the user's last active work chat context if known.
    """
    if message.chat.type == "private" and message.from_user:
        linked_chat_id = AccessControl().get_active_chat(message.from_user.id)
        if linked_chat_id:
            return linked_chat_id
    return message.chat.id


async def should_process_message_in_chat(message: types.Message) -> bool:
    """
    Rules:
    - private chat: always process
    - group/supergroup: only if reply to bot OR explicit mention
    """
    if message.chat.type == "private":
        return True

    bot_user = await message.bot.get_me()

    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot_user.id:
        return True

    text_for_mentions = message.text or message.caption or ""
    entities = message.entities or message.caption_entities
    return is_bot_mentioned_in_entities(text_for_mentions, entities, bot_user.username, bot_user.id)


def is_forwarded_message(message: types.Message) -> bool:
    """Return True when message was forwarded (aiogram 3.x and legacy fields)."""
    if getattr(message, "forward_origin", None):
        return True
    return bool(getattr(message, "forward_from", None) or getattr(message, "forward_sender_name", None))


def should_process_document_upload(message: types.Message, default_should_process: bool) -> bool:
    """
    For usability, forwarded documents in groups should be processed even without
    direct mention/reply to the bot.
    """
    if message.chat.type == "private":
        return True

    if default_should_process:
        return True

    # Core UX requirement: users often forward files from PM/channel.
    if message.chat.type in ["group", "supergroup"] and is_forwarded_message(message):
        return True

    return False


def is_force_run_command(text: str) -> bool:
    """
    Detect explicit user commands to start or restart delegated processing.
    """
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    exact_triggers = {
        "анализ",
        "анализируй",
        "делай",
        "сделай",
        "запускай",
        "начинай",
    }
    if normalized in exact_triggers:
        return True

    phrase_triggers = [
        "сделай анализ",
        "запусти анализ",
        "подготовь документ",
        "сформируй документ",
        "переформируй",
        "перезапусти",
    ]
    return any(trigger in normalized for trigger in phrase_triggers)


def is_explicit_document_request(text: str) -> bool:
    """
    Detect whether the user explicitly asks for a formal document output.
    """
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    doc_markers = [
        "подготовь письмо",
        "составь письмо",
        "сформируй письмо",
        "подготовь претензи",
        "составь претензи",
        "сформируй претензи",
        "подготовь ответ",
        "составь ответ",
        "сформируй ответ",
        "подготовь документ",
        "составь документ",
        "сформируй документ",
        "пришли pdf",
        "сделай pdf",
        "в pdf",
        "официальное письмо",
        "досудебную претензи",
        "иск",
        "договор",
    ]
    return any(marker in normalized for marker in doc_markers)


def enrich_task_description(card: IncidentCard, latest_user_text: str) -> str:
    """
    Keep prior task context and append meaningful clarifications from latest message.
    """
    base = (card.task_description or "").strip()
    latest = (latest_user_text or "").strip()
    if not latest:
        return base or "No description"

    if is_force_run_command(latest):
        return base or latest

    if not base:
        return latest

    # Avoid bloating prompt with duplicate repeats.
    if latest.lower() in base.lower():
        return base

    return f"{base}\nУточнение пользователя: {latest}"


def _extract_tag_block(text: str, tag: str) -> str:
    pattern = rf"\[{re.escape(tag)}\]\s*(.*?)(?=\n\[[A-Z_]+\]|\Z)"
    match = re.search(pattern, text or "", flags=re.S)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_internal_state_json(text: str) -> dict:
    pattern = r"<internal_state>(.*?)</internal_state>"
    match = re.search(pattern, text or "", flags=re.S)
    if not match:
        return {}

    raw_json = (match.group(1) or "").strip()
    try:
        parsed = json.loads(raw_json)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return {}
    return {}


def _strip_internal_state_block(text: str) -> str:
    cleaned = re.sub(r"<internal_state>.*?</internal_state>", "", text or "", flags=re.S)
    return cleaned.strip()


def update_consultation_state_from_strategy(card: IncidentCard) -> None:
    strategy = card.legal_strategy or ""
    internal_state = _extract_internal_state_json(strategy)

    # Preferred format: hidden JSON state block.
    card.current_stage = (internal_state.get("stage") or "").strip() or card.current_stage
    card.known_facts = (internal_state.get("known") or "").strip() or card.known_facts
    card.missing_info = (internal_state.get("missing") or "").strip() or card.missing_info
    card.next_step = (internal_state.get("next_step") or "").strip() or card.next_step
    card.eta_text = (internal_state.get("eta") or "").strip() or card.eta_text
    card.key_risks = (internal_state.get("risks") or "").strip() or card.key_risks

    # Backward-compatible fallback for older tagged outputs.
    card.current_stage = _extract_tag_block(strategy, "STAGE") or card.current_stage
    card.known_facts = _extract_tag_block(strategy, "KNOWN") or card.known_facts
    card.missing_info = _extract_tag_block(strategy, "MISSING") or card.missing_info
    card.next_step = _extract_tag_block(strategy, "NEXT_STEP") or card.next_step
    card.eta_text = _extract_tag_block(strategy, "ETA") or card.eta_text
    card.key_risks = _extract_tag_block(strategy, "RISKS") or card.key_risks


def build_consultation_response(card: IncidentCard) -> str:
    public_text = _strip_internal_state_block(card.legal_strategy or "")

    # Legacy guard: if model returned old tag-only format, use concise fallback text.
    if not public_text or re.search(r"\[[A-Z_]+\]", public_text):
        public_text = "Принято. Провожу правовой анализ и подготовлю следующий шаг."

    lines = [escape(public_text)]

    if card.next_step:
        lines.append(f"\n<b>Следующий шаг:</b> {escape(card.next_step)}")

    if card.missing_info:
        lowered = card.missing_info.lower()
        if all(marker not in lowered for marker in ["не хватает", "нет", "уточн", "нужн"]):
            pass
        else:
            lines.append(f"<b>Нужно от вас:</b> {escape(card.missing_info)}")

    lines.append("\nЕсли нужна конкретика, напишите: <b>«раскрой подробнее»</b>.")
    return "\n".join(lines)

async def chat_with_llm(message: types.Message):
    """
    General chat capabilities with INTELLIGENT ROUTING.
    The LLM decides whether to just talk or to trigger a specific legal workflow.
    """
    llm = get_llm("gpt-4o")
    
    # 1. Bind Tools
    tools = [delegate_task]
    llm_with_tools = llm.bind_tools(tools)
    
    # 2. Get history
    context_chat_id = resolve_context_chat_id(message)
    card = IncidentManager.get_or_create_incident(context_chat_id)
    # Use a wider window so the agent can keep discussion context, while still limiting token size.
    chat_history = card.chat_history[-200:]
    history_entries = []
    
    for msg in chat_history:
        role_label = "System/Bot" if msg.role == "bot" else "User"
        user_label = msg.username if msg.username else role_label
        # Keep richer per-message context for consultation mode.
        content = msg.content[:1500] if msg.content else ""
        if len(msg.content or "") > 1500:
            content += "...(truncated)"
        
        timestamp = msg.timestamp.strftime('%H:%M')
        intro = f"[{timestamp}] {user_label}: "
        history_entries.append(f"{intro}{content}")
    
    chat_context = "\n".join(history_entries)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Ты — Виктор Сергеевич (Viktor), руководитель Юридического департамента ООО «ЗМК» (Telegram-бот «ЗМК-Юрист»). "
                   "Твоя задача — профессионально управлять юридическими вопросами компании.\n\n"
                   "Твой отдел теперь занимается ВСЕМ юридическим сопровождением, а не только рекламациями.\n"
                   "Твой стиль: Деловой, компетентный, вежливый. Ты — опытный управленец.\n\n" 
                   "Твои инструменты (команда):\n"
                   "   - Анна (Секретарь): работа с файлами и OCR.\n"
                   "   - Борис Петрович (Инженер): только ТЕХНИЧЕСКАЯ экспертиза (дефекты, ГОСТ). Не привлекай его к договорам!\n"
                   "   - Елена Владимировна (Юрист): общая правовая работа, договоры, суды, стратегия.\n"
                   "   - Дмитрий (Клерк): оформление красивых официальных документов.\n\n"
                   "СТРАТЕГИЯ:\n"
                   "1. По умолчанию веди КОНСУЛЬТАЦИЮ: обсуждай ситуацию, уточняй факты, объясняй риски и шаги.\n"
                   "2. Не выпускай официальный документ, пока пользователь явно не попросил его подготовить.\n"
                   "3. Используй `delegate_task` для аналитической работы отдела, даже если документ пока не нужен.\n"
                   "4. Выбирай правильный `task_type`:\n"
                   "   - `claim_processing`: если речь идет о БРАКЕ, ДЕФЕКТАХ, РЕКЛАМАЦИЯХ. (Нужен Борис Петрович).\n"
                   "   - `document_drafting`: только если пользователь ЯВНО просит подготовить официальный документ (договор, письмо, претензия, иск).\n"
                   "   - `consultation`: если нужно обсудить кейс, собрать данные, понять позицию или дать рекомендации без формального документа.\n"
                   "   - `legal_advice`: если нужен развернутый юридический разбор, но без обязательного выпуска документа.\n"
                   "5. Не используй шаблонные фразы вроде 'мы всегда готовы помочь' без фактического содержания.\n"
                   "6. По умолчанию отвечай коротко: 1-3 абзаца, без лишней формализации.\n"
                   "7. Если спрашивают про сроки/статус, отвечай конкретно: этап, следующий шаг, срок или условие срока.\n"),
        ("user", "История чата:\n{context}\n\nПоследнее сообщение: {text}")
    ])
    
    chain = prompt | llm_with_tools
    
    try:
        # 3. Get LLM Decision
        # Use ainvoke with robust error handling
        ai_msg = await chain.ainvoke({"text": message.text, "context": chat_context})
        
        # 4. Check for Tool Calls
        if ai_msg.tool_calls:
            # The LLM decided to work!
            tool_call = ai_msg.tool_calls[0]
            if tool_call["name"] == "delegate_task":
                args = tool_call["args"]
                t_type = args.get("task_type", "consultation")
                desc = args.get("description", "No description")
                wants_document = is_explicit_document_request((message.text or "") + "\n" + desc)

                # Safety: do not jump into drafting unless user explicitly asked for a document.
                if t_type == "document_drafting" and not wants_document:
                    t_type = "consultation"
                elif wants_document and t_type in ["consultation", "legal_advice"]:
                    t_type = "document_drafting"
                
                await message.answer(f"🔄 Вас понял. Поручаю задачу отделу: {human_task_type(t_type)}...")
                
                # Update card context
                card.task_type = t_type
                card.task_description = desc
                IncidentManager.update_incident(context_chat_id, card)
                
                await run_delegated_task(message, card, generate_document=wants_document)
                
                # Bot record
                IncidentManager.add_message(context_chat_id, "bot", f"Поручена задача отделу: {human_task_type(t_type)}", "ZMK_Bot")
                return
        
        # 5. Normal Response (Just talk)
        response_text = ai_msg.content
        if not response_text:
             # Fallback if LLM tried to call tool but failed or sent empty content
             response_text = "Принято. Работаем."
             
        IncidentManager.add_message(context_chat_id, "bot", response_text, "ZMK_Bot")
        await message.answer(response_text)
        
    except Exception as e:
        logger.error(f"Chat LLM Error: {e}", exc_info=True)
        # Show specific error to user for debugging (temporary)
        await message.answer(f"⚠️ Ошибка в модуле решений (LLM): {str(e)}")

async def run_delegated_task(message: types.Message, card: IncidentCard, generate_document: bool = False):
    """
    Orchestrates the workflow based on task type.
    """
    status_msg = await message.answer(f"📂 Начинаю работу по задаче: {human_task_type(card.task_type)}...")
    
    # --- Workflow 1: Claim Processing (Standard Pipeline) ---
    if card.task_type in ["claim_processing", "claim"]:
        # 1. Technical Analysis
        await status_msg.edit_text("⚙️ <b>Борис Петрович (Инженер)</b> анализирует дефекты...")
        card = await engineer.run(card)
        
        # 2. Legal Analysis
        await status_msg.edit_text("⚖️ <b>Елена Владимировна (Юрист)</b> оценивает риски...")
        card = await lawyer.run(card)
        
        # 3. Drafting only on explicit user request
        if generate_document:
            await status_msg.edit_text("📝 <b>Дмитрий (Документовед)</b> готовит ответ...")
            card = await clerk.run(card)
        else:
            card.generated_response = None

    # --- Workflow 2: General Document Drafting / Legal Advice ---
    elif card.task_type in ["document_drafting", "legal_advice", "consultation"]:
        # Skip Engineer!
        # 1. Legal Analysis / Strategy
        await status_msg.edit_text("⚖️ <b>Елена Владимировна (Юрист)</b> прорабатывает правовую позицию...")
        # Lawyer needs to know what to do based on task_description, not technical_verdict
        # We might need to update LawyerAgent to handle this, or mocking it here.
        # Ideally, LawyerAgent should see the `task_description`.
        card = await lawyer.run(card)
        
        # 2. Drafting only when explicitly requested or task requires a formal document.
        should_draft = card.task_type == "document_drafting" or generate_document
        if should_draft:
            await status_msg.edit_text("📝 <b>Дмитрий (Документовед)</b> составляет документ...")
            card = await clerk.run(card)
        else:
            card.generated_response = None

    # Final Result
    await status_msg.delete()

    def has_pipeline_error(current_card: IncidentCard) -> bool:
        """
        Detect known error markers across pipeline outputs.
        """
        fields = [
            current_card.technical_verdict,
            current_card.legal_strategy,
            current_card.generated_response,
        ]
        markers = ["error", "ошибка", "не удалось"]
        for value in fields:
            if not value:
                continue
            lowered = value.lower()
            if any(marker in lowered for marker in markers):
                return True
        return False

    pipeline_failed = has_pipeline_error(card)

    # Keep an operational consultation state for grounded status replies.
    update_consultation_state_from_strategy(card)
    
    # Persist latest state to keep long-term context per chat.
    context_chat_id = resolve_context_chat_id(message)
    IncidentManager.update_incident(context_chat_id, card)

    # Display text preview
    if pipeline_failed:
        result_text = (
            f"⚠️ <b>Частичный результат:</b>\n\n"
            f"<code>{card.generated_response}</code>\n\n"
            f"Файл не формирую: в одном из этапов возникла ошибка."
        )
    elif not generate_document:
        result_text = build_consultation_response(card)
    else:
        result_text = (
            f"✅ <b>Готово:</b>\n\n"
            f"<code>{card.generated_response}</code>\n\n"
            f"Проверяю маршрут согласования..."
        )
    await message.answer(result_text)

    # --- Route by review rules ---
    if generate_document and card.generated_response and not pipeline_failed:
        mode = review_queue.get_rule(card.task_type)

        if mode == "manual":
            task_id = review_queue.enqueue(
                chat_id=message.chat.id,
                requester_user_id=message.from_user.id,
                requester_name=message.from_user.full_name,
                task_type=card.task_type,
                content=card.generated_response,
            )

            await message.answer(
                f"🕒 Документ поставлен в очередь согласования юристом. Номер: <b>#{task_id}</b>."
            )

            reviewer_text = (
                f"📥 <b>Новая задача на проверку</b>\n"
                f"ID: <b>#{task_id}</b>\n"
                f"Тип: {human_task_type(card.task_type)}\n"
                f"От: {message.from_user.full_name}\n\n"
                f"Для выпуска: /review_approve {task_id}\n"
                f"Для отклонения: /review_reject {task_id} причина"
            )
            for reviewer_id in LAWYER_REVIEWER_IDS:
                try:
                    await message.bot.send_message(reviewer_id, reviewer_text)
                except Exception as e:
                    logger.warning(f"Could not notify reviewer {reviewer_id}: {e}")
            return

        sent = await send_pdf_to_chat(
            message=message,
            chat_id=message.chat.id,
            text=card.generated_response,
            caption_prefix="Подготовлен юридическим департаментом.",
        )
        if not sent:
            await message.answer("⚠️ Ошибка: Не удалось создать PDF файл.")
    elif generate_document and card.generated_response:
        await message.answer("⚠️ Документ не был сформирован полностью. Проверьте формулировку запроса и повторите запуск.")


@router.message(Command("review_rules"), IsAllowedUser())
async def review_rules_handler(message: types.Message):
    if not is_reviewer(message.from_user.id):
        return

    rules = review_queue.list_rules()
    if not rules:
        await message.answer("Правила согласования пока не настроены.")
        return

    lines = ["⚙️ <b>Правила согласования</b>"]
    for task_type, mode in rules:
        mode_ru = "ручная проверка" if mode == "manual" else "автовыпуск"
        lines.append(f"• {human_task_type(task_type)}: {mode_ru}")
    lines.append("\nИзменить: /review_set <task_type> <auto|manual>")
    await message.answer("\n".join(lines))


@router.message(Command("review_set"), IsAllowedUser())
async def review_set_handler(message: types.Message):
    if not is_reviewer(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Формат: /review_set <task_type> <auto|manual>")
        return

    task_type = parts[1].strip()
    mode = parts[2].strip().lower()
    if mode not in ["auto", "manual"]:
        await message.answer("Режим может быть только auto или manual.")
        return

    review_queue.set_rule(task_type, mode)
    mode_ru = "ручная проверка" if mode == "manual" else "автовыпуск"
    await message.answer(f"Обновлено: {human_task_type(task_type)} -> {mode_ru}")


@router.message(Command("review_queue"), IsAllowedUser())
async def review_queue_handler(message: types.Message):
    if not is_reviewer(message.from_user.id):
        return

    tasks = review_queue.list_pending(limit=20)
    if not tasks:
        await message.answer("Очередь согласования пуста.")
        return

    lines = ["📋 <b>Очередь согласования</b>"]
    for task in tasks:
        lines.append(
            f"• #{task.id} | {human_task_type(task.task_type)} | чат {task.chat_id} | {task.requester_name}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("review_approve"), IsAllowedUser())
async def review_approve_handler(message: types.Message):
    if not is_reviewer(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /review_approve <id>")
        return

    task_id = int(parts[1])
    task = review_queue.get_task(task_id)
    if not task or task.status != "pending":
        await message.answer("Задача не найдена или уже обработана.")
        return

    review_queue.approve(task_id, message.from_user.id)

    sent = await send_pdf_to_chat(
        message=message,
        chat_id=task.chat_id,
        text=task.content,
        caption_prefix="Подготовлен юридическим департаментом и согласован юристом.",
    )
    if sent:
        await message.answer(f"✅ Задача #{task_id} согласована и отправлена в чат {task.chat_id}.")
    else:
        await message.answer(f"⚠️ Задача #{task_id} согласована, но отправка PDF не удалась.")


@router.message(Command("review_reject"), IsAllowedUser())
async def review_reject_handler(message: types.Message):
    if not is_reviewer(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Формат: /review_reject <id> <причина>")
        return

    task_id = int(parts[1])
    reason = parts[2].strip()

    task = review_queue.get_task(task_id)
    if not task or task.status != "pending":
        await message.answer("Задача не найдена или уже обработана.")
        return

    review_queue.reject(task_id, message.from_user.id, reason)

    try:
        await message.bot.send_message(
            task.chat_id,
            f"❌ Документ по задаче #{task_id} отклонен юристом. Причина: {reason}",
        )
    except Exception as e:
        logger.warning(f"Could not notify source chat {task.chat_id}: {e}")

    await message.answer(f"✅ Задача #{task_id} отклонена.")
    
@router.message(F.document | F.photo, IsAllowedUser())
async def handle_document_upload(message: types.Message):
    """
    Handles file upload by sending it to Secretary Agent.
    """
    chat_id = resolve_context_chat_id(message)

    default_should_process = await should_process_message_in_chat(message)
    if not should_process_document_upload(message, default_should_process):
        logger.info(f"Ignored document from {message.from_user.id} in group without mention/reply.")
        return
    
    # 1. Extract File Info
    file_id = ""
    file_name = f"unknown_{message.date.isoformat()}"
    file_type = "unknown"
    
    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or file_name
        file_type = message.document.mime_type or "application/octet-stream"
    elif message.photo:
        # Get highest resolution photo
        photo = message.photo[-1]
        file_id = photo.file_id
        file_name = f"photo_{file_id}.jpg"
        file_type = "image/jpeg"

    # 2. Add to incident card via Secretary
    doc_info = DocumentInfo(
        file_id=file_id,
        file_name=file_name,
        file_type=file_type
    )
    
    # Record upload in history
    IncidentManager.add_message(
        chat_id=chat_id,
        role="system",
        content=f"User uploaded document/photo: {file_name}",
        username="System"
    )
    
    input_data = {
        "chat_id": chat_id,
        "file": doc_info,
        "text": message.caption or ""
    }
    
    updated_card: IncidentCard = await secretary.run(input_data)
    updated_card.required_documents = secretary.get_required_documents(updated_card)
    IncidentManager.update_incident(chat_id, updated_card)
    
    # 3. Check for completeness and notify user
    missing = secretary.check_completeness(updated_card)
    
    response_text = (f"📎 Документ <b>{file_name}</b> принят.\n")
    
    if missing:
        response_text += (f"⚠️ Для полноценного анализа не хватает: {', '.join(missing)}\n")
        response_text += "Вы можете загрузить остальные документы или написать <b>«Анализ»</b>, чтобы работать с тем, что есть."
        await message.answer(response_text)
    else:
        await message.answer(response_text + "\n✅ Все документы собраны! Напишите <b>«Анализ»</b> для старта работы.")
    
    # SAFETY: Do NOT trigger run_delegated_task automatically on file upload. 
    # It causes budget drain if user uploads multiple files in a row (n files * 3 agents = $$$).
    # Wait for explicit user confirmation via text message.

@router.message(F.text & ~F.text.startswith('/'), IsAllowedUser())
async def handle_text_message(message: types.Message):
    """
    Handle plain text messages.
    1. Always record message to history (for context).
    2. If Forwarded -> Record with [Forwarded from ...] prefix. Do NOT reply in groups (to avoid spam).
    3. If Private Chat -> Find intent via LLM (Chat).
    4. If Group Chat -> Check keywords OR mention. Only reply if relevant to work.
    """
    # 1. Detect Forwarding
    forward_label = ""
    is_forwarded = False
    
    # Check for forward origin (Aiogram 3.x)
    if message.forward_origin:
        is_forwarded = True
        origin = message.forward_origin
        if origin.type == "user":
            name = origin.sender_user.full_name
            forward_label = f"[Forwarded from {name}]: "
        elif origin.type == "hidden_user":
            name = origin.sender_user_name
            forward_label = f"[Forwarded from {name}]: "
        elif origin.type == "chat":
            name = origin.chat.title or "Channel"
            forward_label = f"[Forwarded from {name}]: "
        elif origin.type == "channel":
             name = origin.chat.title or "Channel"
             forward_label = f"[Forwarded from {name}]: "
    # Fallback for older attributes if forward_origin is somehow missing but is_forwarded might be inferable
    elif message.forward_from or message.forward_sender_name:
         is_forwarded = True
         name = message.forward_from.full_name if message.forward_from else message.forward_sender_name
         forward_label = f"[Forwarded from {name}]: "

    context_chat_id = resolve_context_chat_id(message)

    final_content = forward_label + message.text

    # 2. Record incoming message
    IncidentManager.add_message(
        chat_id=context_chat_id,
        role="user",
        content=final_content,
        username=message.from_user.full_name
    )

    text = message.text.lower()
    should_reply = await should_process_message_in_chat(message)
    card = IncidentManager.get_or_create_incident(context_chat_id)

    # Deterministic trigger for explicit user commands like "анализ" / "делай".
    if should_reply and is_force_run_command(message.text or ""):
        has_context = bool(card.uploaded_documents or card.task_description)
        if has_context:
            card.task_description = enrich_task_description(card, message.text)
            if card.task_type in ["claim", "claim_processing"] and "договор" in card.task_description.lower():
                card.task_type = "document_drafting"

            IncidentManager.update_incident(context_chat_id, card)
            wants_document = is_explicit_document_request(message.text or "")
            run_mode = "подготовку документа" if wants_document else "консультационный разбор"
            await message.answer(f"🔄 Принято. Запускаю {run_mode} с учетом ваших уточнений...")
            await run_delegated_task(message, card, generate_document=wants_document)
            return
    
    # Direct PDF generation trigger (legacy override)
    if should_reply and ("пдф" in text or "pdf" in text or "письмо" in text) and ("сделай" in text or "сформируй" in text or "пришли" in text):
         # Получаем текущее состояние
         card = IncidentManager.get_or_create_incident(context_chat_id)
         
         # Guess task type if not set
         if not card.task_type or card.task_type == "claim": # default "claim" might be old value
              # If user asks for general letter, maybe switch to document_drafting?
              # For safety, let the LLM handle it via chat_with_llm if possible, OR default to claim.
              # Let's default to claim_processing for backward compatibility unless LLM intervenes.
              if "договор" in text or "иск" in text:
                  card.task_type = "document_drafting"
                  card.task_description = text
              else:
                  card.task_type = "claim_processing"
                  
         await message.answer("🔄 Принято. Начинаю формирование документа...")
         await run_delegated_task(message, card, generate_document=True)
         return 

    # В личке или при ответе - всегда слушаем LLM
    if should_reply:
        await chat_with_llm(message)
    else:
        logger.info(f"Recorded message from {message.from_user.id} in group (Forwarded={is_forwarded}). Silent mode.")
