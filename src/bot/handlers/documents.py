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

async def chat_with_llm(message: types.Message):
    """
    General chat capabilities with INTELLIGENT ROUTING.
    The LLM decides whether to just talk or to trigger the document pipeline.
    """
    llm = get_llm("gpt-4o")
    
    # 1. Define Tools
    from langchain_core.tools import tool

    @tool
    async def start_analysis_pipeline(justification: str):
        """
        Call this tool ONLY when the user asks to generate a document, create a PDF, 
        write an official response, formulate an opinion, or analyze the case formally.
        This triggers the Secretary -> Engineer -> Lawyer -> Clerk workflow.
        """
        # This will be handled in the execution block
        return "PIPELINE_TRIGGERED"

    tools = [start_analysis_pipeline]
    llm_with_tools = llm.bind_tools(tools)
    
    # 2. Get history
    card = IncidentManager.get_or_create_incident(message.chat.id)
    chat_history = card.chat_history[-50:]
    history_entries = []
    
    for msg in chat_history:
        role_label = "System/Bot" if msg.role == "bot" else "User"
        user_label = msg.username if msg.username else role_label
        timestamp = msg.timestamp.strftime('%H:%M')
        intro = f"[{timestamp}] {user_label}: "
        content = msg.content
        history_entries.append(f"{intro}{content}")
    
    chat_context = "\n".join(history_entries)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Ты — Виктор Сергеевич (Viktor), руководитель цифрового юридического отдела ООО «ЗМК» (Telegram-бот «ЗМК-Юрист»). "
                   "Твоя задача — координировать работу команды и принимать решения.\n\n"
                   "Твой стиль общения: Деловой, уверенный, конструктивный. Ты — 'лицо' отдела.\n\n" 
                   "У тебя есть доступ к инструменту `start_analysis_pipeline`. Это 'волшебная кнопка', которая запускает твоих сотрудников в работу.\n"
                   "Твоя стратегия:\n"
                   "1. Если пользователь просто задает вопрос или болтает — отвечай сам, опираясь на контекст переписки.\n"
                   "2. Если пользователь просит ДЕЙСТВИЙ ('Сформировать мнение', 'Дать ответ', 'Сделать документ', 'Проанализировать', 'Прислать скрин/пдф') — ОБЯЗАТЕЛЬНО вызывай `start_analysis_pipeline`.\n"
                   "3. Не говори 'Я сейчас передам дело', если ты не вызываешь инструмент. Если вызываешь — просто вызови его молча (система сама уведомит пользователя).\n"
                   "4. Твоя команда (ты ей управляешь):\n"
                   "   - Анна (Секретарь): сбор документов.\n"
                   "   - Борис Петрович (Инженер): технический анализ.\n"
                   "   - Елена Владимировна (Юрист): правовая оценка.\n"
                   "   - Дмитрий (Клерк): оформление бумаг."),
        ("user", "История чата:\n{context}\n\nПоследнее сообщение: {text}")
    ])
    
    chain = prompt | llm_with_tools
    
    try:
        # 3. Get LLM Decision
        ai_msg = await chain.ainvoke({"text": message.text, "context": chat_context})
        
        # 4. Check for Tool Calls
        if ai_msg.tool_calls:
            # The LLM decided to work!
            tool_call = ai_msg.tool_calls[0]
            if tool_call["name"] == "start_analysis_pipeline":
                # User wants action -> Trigger Pipeline
                 await message.answer("🔄 Вас понял. Запускаю процедуру формирования ответа и ПДФ...")
                 await run_analysis_pipeline(message, card)
                 # Bot record
                 IncidentManager.add_message(message.chat.id, "bot", "Started analysis pipeline (Tool Call)", "ZMK_Bot")
                 return
        
        # 5. Normal Response (Just talk)
        response_text = ai_msg.content
        if not response_text:
             # Fallback if LLM tried to call tool but failed or sent empty content
             response_text = "Принято."
             
        IncidentManager.add_message(message.chat.id, "bot", response_text, "ZMK_Bot")
        await message.answer(response_text)
        
    except Exception as e:
        logger.error(f"Chat LLM Error: {e}")
        await message.answer("Извините, произошла ошибка в модуле управления.")

async def run_analysis_pipeline(message: types.Message, card: IncidentCard):
    """
    Orchestrates the analysis pipeline once documents are ready.
    """
    status_msg = await message.answer("🔄 Документы собраны. Передаю дело специалистам...")
    
    # 1. Technical Analysis
    await status_msg.edit_text("⚙️ <b>Борис Петрович (Инженер)</b> изучает фото дефектов...")
    card = await engineer.run(card)
    
    # 2. Legal Analysis
    await status_msg.edit_text("⚖️ <b>Елена Владимировна (Юрист)</b> строит правовую позицию...")
    card = await lawyer.run(card)
    
    # 3. Document Drafting
    await status_msg.edit_text("📝 <b>Дмитрий (Документовед)</b> готовит официальный ответ...")
    card = await clerk.run(card)
    
    # Final Result
    await status_msg.delete()
    
    # Display text preview
    result_text = (
        f"✅ <b>Готов проект ответа:</b>\n\n"
        f"<code>{card.generated_response}</code>\n\n"
        f"Генерирую PDF..."
    )
    await message.answer(result_text)

    # --- Generate and Send PDF ---
    if card.generated_response:
        pdf_filename = f"ZMK_Response_{card.chat_id}_{message.message_id}.pdf"
        temp_dir = tempfile.gettempdir()
        full_pdf_path = os.path.join(temp_dir, pdf_filename)
        
        try:
            # Run PDF generation in a thread to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            success = await loop.run_in_executor(None, create_pdf, card.generated_response, full_pdf_path)
            
            if success and os.path.exists(full_pdf_path):
                file_to_send = FSInputFile(full_pdf_path)
                await message.answer_document(
                    document=file_to_send, 
                    caption="📄 <b>Официальный ответ (PDF)</b>\nСформирован автоматически."
                )
                # Cleanup after sending
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
        # Trigger pipeline
        await run_analysis_pipeline(message, updated_card)

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
        "пдф", "сформируй", "ответ", "письмо", "проект", "составь", "сделай"
    ]
    
    is_private = message.chat.type == "private"
    is_relevant = any(k in text for k in keywords)
    is_reply = message.reply_to_message and message.reply_to_message.from_user.is_bot
    
    # Logic for replying
    should_reply = False
    
    # Check if explicitly addressed (keywords, bot name, or reply)
    # Adding specific triggers for the persona
    triggers = ["виктор", "сергеевич", "бот", "bot", "змк", "юрист", "@"]
    is_addressed = any(t in text for t in triggers)

    if is_private:
        should_reply = True
    elif is_reply:
        should_reply = True
    elif is_addressed or is_relevant:
        # If mentioned by name OR discussing work topics (keywords) in the group
        should_reply = True
    
    # ПРИНУДИТЕЛЬНЫЙ ЗАПУСК PDF (На всякий случай оставим, но LLM теперь умнее)
    if ("пдф" in text or "pdf" in text or "письмо" in text) and ("сделай" in text or "сформируй" in text or "пришли" in text):
         # Получаем текущее состояние
         card = IncidentManager.get_or_create_incident(message.chat.id)
         await message.answer("🔄 Принято. Начинаю формирование документа...")
         await run_analysis_pipeline(message, card)
         return # Завершаем, чтобы не дублировалось через LLM

    # В личке или при ответе - всегда слушаем LLM
    if should_reply:
        # В личке или при явном обращении бот "умный" и общительный через LLM
        await chat_with_llm(message)
    else:
        # В остальных случаях (включая пересланные сообщения в группах) — молчим и записываем
        # Можно добавить лог, чтобы видеть, что сообщение обработано
        logger.info(f"Recorded message from {message.from_user.id} in group (Forwarded={is_forwarded}). Silent mode.")
