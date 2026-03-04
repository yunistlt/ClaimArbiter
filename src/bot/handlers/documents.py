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
from aiogram.types import FSInputFile
from bot.filters import IsAllowedUser
import asyncio
import os
import tempfile
import logging

router = Router()
secretary = SecretaryAgent()
engineer = EngineerAgent()
lawyer = LawyerAgent()
clerk = ClerkAgent()

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
        description (str): A summary of what needs to be done.
    """
    return "TASK_DELEGATED"

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
    card = IncidentManager.get_or_create_incident(message.chat.id)
    chat_history = card.chat_history[-20:] # Reduced from 50 to avoid token limits with large docs
    history_entries = []
    
    for msg in chat_history:
        role_label = "System/Bot" if msg.role == "bot" else "User"
        user_label = msg.username if msg.username else role_label
        # Clean content to avoid massive tokens
        content = msg.content[:1000] if msg.content else ""
        if len(msg.content) > 1000: content += "...(truncated)"
        
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
                   "1. Если вопрос простой или справочный — ответь сам.\n"
                   "2. Если требуется ОФИЦИАЛЬНЫЙ ДОКУМЕНТ или СЛОЖНЫЙ АНАЛИЗ — используй `delegate_task`.\n"
                   "3. Выбирай правильный `task_type`:\n"
                   "   - `claim_processing`: если речь идет о БРАКЕ, ДЕФЕКТАХ, РЕКЛАМАЦИЯХ. (Нужен Борис Петрович).\n"
                   "   - `document_drafting`: если просят составить ДОГОВОР, ПИСЬМО (не по браку), ИСК. (Инженер НЕ нужен).\n"
                   "   - `legal_advice`: если нужен развернутый юридический совет.\n"),
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
                t_type = args.get("task_type", "claim_processing")
                desc = args.get("description", "No description")
                
                await message.answer(f"🔄 Вас понял. Поручаю задачу отделу: {t_type}...")
                
                # Update card context
                card.task_type = t_type
                card.task_description = desc
                IncidentManager.update_incident(message.chat.id, card)
                
                await run_delegated_task(message, card)
                
                # Bot record
                IncidentManager.add_message(message.chat.id, "bot", f"Delegated task: {t_type}", "ZMK_Bot")
                return
        
        # 5. Normal Response (Just talk)
        response_text = ai_msg.content
        if not response_text:
             # Fallback if LLM tried to call tool but failed or sent empty content
             response_text = "Принято. Работаем."
             
        IncidentManager.add_message(message.chat.id, "bot", response_text, "ZMK_Bot")
        await message.answer(response_text)
        
    except Exception as e:
        logger.error(f"Chat LLM Error: {e}", exc_info=True)
        # Show specific error to user for debugging (temporary)
        await message.answer(f"⚠️ Ошибка в модуле решений (LLM): {str(e)}")

async def run_delegated_task(message: types.Message, card: IncidentCard):
    """
    Orchestrates the workflow based on task type.
    """
    status_msg = await message.answer(f"📂 Начинаю работу по задаче: {card.task_type}...")
    
    # --- Workflow 1: Claim Processing (Standard Pipeline) ---
    if card.task_type == "claim_processing":
        # 1. Technical Analysis
        await status_msg.edit_text("⚙️ <b>Борис Петрович (Инженер)</b> анализирует дефекты...")
        card = await engineer.run(card)
        
        # 2. Legal Analysis
        await status_msg.edit_text("⚖️ <b>Елена Владимировна (Юрист)</b> оценивает риски...")
        card = await lawyer.run(card)
        
        # 3. Drafting
        await status_msg.edit_text("📝 <b>Дмитрий (Документовед)</b> готовит ответ...")
        card = await clerk.run(card)

    # --- Workflow 2: General Document Drafting / Legal Advice ---
    elif card.task_type in ["document_drafting", "legal_advice"]:
        # Skip Engineer!
        # 1. Legal Analysis / Strategy
        await status_msg.edit_text("⚖️ <b>Елена Владимировна (Юрист)</b> прорабатывает правовую позицию...")
        # Lawyer needs to know what to do based on task_description, not technical_verdict
        # We might need to update LawyerAgent to handle this, or mocking it here.
        # Ideally, LawyerAgent should see the `task_description`.
        card = await lawyer.run(card)
        
        # 2. Drafting (if needed)
        if card.task_type == "document_drafting" or card.generated_response is None:
             await status_msg.edit_text("📝 <b>Дмитрий (Документовед)</b> составляет документ...")
             card = await clerk.run(card)

    # Final Result
    await status_msg.delete()
    
    # Display text preview
    result_text = (
        f"✅ <b>Готово:</b>\n\n"
        f"<code>{card.generated_response}</code>\n\n"
        f"Формирую файл..."
    )
    await message.answer(result_text)

    # --- Generate and Send PDF ---
    if card.generated_response:
        pdf_filename = f"ZMK_Doc_{card.chat_id}_{message.message_id}.pdf"
        temp_dir = tempfile.gettempdir()
        full_pdf_path = os.path.join(temp_dir, pdf_filename)
        
        try:
            # Run PDF generation in a thread
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, create_pdf, card.generated_response, full_pdf_path)
            
            if success and os.path.exists(full_pdf_path):
                file_to_send = FSInputFile(full_pdf_path)
                await message.answer_document(
                    document=file_to_send, 
                    caption="📄 <b>Документ (PDF)</b>\nПодготовлен юридическим департаментом."
                )
                os.remove(full_pdf_path)
            else:
                await message.answer("⚠️ Ошибка: Не удалось создать PDF файл.")
        except Exception as e:
            logger.error(f"Error generating PDF: {e}")
            await message.answer("⚠️ Произошла ошибка при генерации PDF.")
    
@router.message(F.document | F.photo, IsAllowedUser())
async def handle_document_upload(message: types.Message):
    """
    Handles file upload by sending it to Secretary Agent.
    """
    chat_id = message.chat.id
    
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
    
    # 3. Check for completeness and notify user
    missing = secretary.check_completeness(updated_card)
    
    response_text = (f"📎 Документ <b>{file_name}</b> принят.\n")
    
    if missing:
        response_text += (f"⚠️ Не хватает: {', '.join(missing)}")
        await message.answer(response_text)
    else:
        await message.answer(response_text + "\n✅ Все документы собраны!")
        # Automatically trigger pipeline if documents are complete (assuming claim by default for uploads)
        await run_delegated_task(message, updated_card)

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

    final_content = forward_label + message.text

    # 2. Record incoming message
    IncidentManager.add_message(
        chat_id=message.chat.id,
        role="user",
        content=final_content,
        username=message.from_user.full_name
    )

    text = message.text.lower()
    keywords = [
        "претензия", "брак", "дефект", "сломалось", "не работает", "возврат", "рекламац", "ошибка", "проблема",
        "анализ", "провер", "статус", "ситуаци", "помощь", "бот", "@zmkclaim_bot",
        "пдф", "сформируй", "ответ", "письмо", "проект", "составь", "сделай", "договор", "иск", "юрист", "консультац"
    ]
    
    is_private = message.chat.type == "private"
    is_relevant = any(k in text for k in keywords)
    is_reply = message.reply_to_message and message.reply_to_message.from_user.is_bot
    
    # Logic for replying
    should_reply = False
    
    # Check if explicitly addressed (keywords, bot name, or reply)
    triggers = ["виктор", "сергеевич", "бот", "bot", "змк", "юрист", "@"]
    is_addressed = any(t in text for t in triggers)

    if is_private:
        should_reply = True
    elif is_reply:
        should_reply = True
    elif is_addressed or is_relevant:
        should_reply = True
    
    # Direct PDF generation trigger (legacy override)
    if ("пдф" in text or "pdf" in text or "письмо" in text) and ("сделай" in text or "сформируй" in text or "пришли" in text):
         # Получаем текущее состояние
         card = IncidentManager.get_or_create_incident(message.chat.id)
         
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
         await run_delegated_task(message, card)
         return 

    # В личке или при ответе - всегда слушаем LLM
    if should_reply:
        await chat_with_llm(message)
    else:
        logger.info(f"Recorded message from {message.from_user.id} in group (Forwarded={is_forwarded}). Silent mode.")
