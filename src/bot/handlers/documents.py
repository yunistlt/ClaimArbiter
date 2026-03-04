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
import os
import tempfile
import logging

router = Router()
secretary = SecretaryAgent()
engineer = EngineerAgent()
lawyer = LawyerAgent()
clerk = ClerkAgent()

logger = logging.getLogger(__name__)

async def chat_with_llm(message: types.Message):
    """
    General chat capabilities for the bot.
    Now uses chat history context!
    """
    llm = get_llm("gpt-4o")
    
    # Get history - Increased to 50 messages for better context
    card = IncidentManager.get_or_create_incident(message.chat.id)
    chat_history = card.chat_history[-50:]
    history_entries = []
    
    for msg in chat_history:
        role_label = "System/Bot" if msg.role == "bot" else "User"
        # If username is set and different from generic User label, use it
        user_label = msg.username if msg.username else role_label
        timestamp = msg.timestamp.strftime('%H:%M')
        # Check if content already suggests it's a forward, if not, relying on role/username
        history_entries.append(f"[{timestamp}] {user_label}: {msg.content}")
    
    chat_context = "\n".join(history_entries)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Ты — Telegram-бот «ЗМК-Юрист», координатор цифрового юридического отдела ООО «ЗМК». "
                   "Твоя задача — быть лицом отдела и общаться с сотрудниками. "
                   "Ты вежлив, компетентен и готов помочь. Твои ответы должны быть логичны и опираться на контекст.\n"
                   "Твоя команда (виртуальные агенты):\n"
                   "1. Анна (Секретарь) — педантичная, следит за наличием документов (ТОРГ-12, акты), первой принимает файлы.\n"
                   "2. Борис Петрович (Инженер) — опытный специалист, оценивает техническую сторону брака, сверяет с ГОСТами. Строг, но справедлив.\n"
                   "3. Елена Владимировна (Юрист) — защищает интересы компании, ищет пункты договора про просрочки и нарушения приемки.\n"
                   "4. Дмитрий (Клерк) — составляет официальные письма и претензии канцелярским языком.\n\n"
                   "ВАЖНО ПРО КОНТЕКСТ:\n"
                   "1. Тебе передается история чата. Читай её внимательно.\n"
                   "2. Сообщения, помеченные как [Forwarded from ...], — это пересланные сообщения (например, от клиента или другого сотрудника). Анализируй их как входные данные/факты, а не как прямую речь текущего пользователя.\n"
                   "3. Если пользователь переслал переписку и спрашивает «Проанализируй» или «Что думаешь?», используй пересланные сообщения как материал для анализа.\n"
                   "4. Твой ответ должен быть кратким, по делу. Если видишь проблему/брак — предлагай прислать фото/документы (если их еще нет).\n"
                   "5. Если тебя спрашивают, кто в твоей команде или как ты работаешь — подробно расскажи про Анну, Бориса, Елену и Дмитрия.\n"
                   "6. Если тебя спрашивают, можно ли добавить в чат — отвечай утвердительно.\n"
                   "7. Ты умеешь не только разбирать рекламации, но и помогать с **любыми деловыми письмами**. Если тебя просят составить ответ клиенту, письмо-уведомление или другое официальное сообщение — Дмитрий (Клерк) с радостью поможет сформулировать текст в деловом стиле."),
        ("user", "История чата (последние 50 сообщений):\n{context}\n\nТекущий запрос: {text}")
    ])
    
    chain = prompt | llm | StrOutputParser()
    
    try:
        response = await chain.ainvoke({"text": message.text, "context": chat_context})
        
        # Record bot's answer too
        IncidentManager.add_message(message.chat.id, "bot", response, "ZMK_Bot")
        
        await message.answer(response)
    except Exception as e:
        logger.error(f"Chat LLM Error: {e}")
        await message.answer("Извините, я задумался и не смог сформулировать ответ.")

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
            success = create_pdf(card.generated_response, full_pdf_path)
            
            if success and os.path.exists(full_pdf_path):
                file_to_send = FSInputFile(full_pdf_path)
                await message.answer_document(
                    document=file_to_send, 
                    caption="📄 <b>Официальный ответ (PDF)</b>\nСформирован автоматически."
                )
                # Cleanup after sending (using asyncio sleep to ensure send completes? No, await blocks until send)
                os.remove(full_pdf_path)
            else:
                await message.answer("⚠️ Ошибка: Не удалось создать PDF файл.")
        except Exception as e:
            logger.error(f"Error generating PDF: {e}")
            await message.answer("⚠️ Произошла ошибка при генерации PDF.")
    
@router.message(F.document | F.photo)
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

@router.message(F.text & ~F.text.startswith('/'))
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
        "анализ", "провер", "статус", "ситуаци", "помощь", "бот", "@zmkclaim_bot"
    ]
    
    is_private = message.chat.type == "private"
    is_relevant = any(k in text for k in keywords)
    is_reply = message.reply_to_message and message.reply_to_message.from_user.is_bot
    
    # Logic for replying
    should_reply = False
    
    if is_private:
        should_reply = True
    elif is_reply:
        should_reply = True
    elif is_relevant and not is_forwarded:
         # Only reply to relevant keywords in groups IF IT IS NOT A FORWARD
         # Forwards are treated as data ingestion. User must ask explicitly to analyze.
         should_reply = True
         
    if should_reply:
        # В личке или при явном обращении бот "умный" и общительный через LLM
        await chat_with_llm(message)
    else:
        # В остальных случаях (включая пересланные сообщения в группах) — молчим и записываем
        # Можно добавить лог, чтобы видеть, что сообщение обработано
        logger.info(f"Recorded message from {message.from_user.id} in group (Forwarded={is_forwarded})")
