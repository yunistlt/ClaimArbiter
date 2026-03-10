from agents.base import BaseAgent
from models import IncidentCard, DocumentInfo
from services.incident_manager import IncidentManager
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

class SecretaryAgent(BaseAgent):
    """
    Агент «Секретарь» (Анна):
    - Распознает текст (OCR) со сканов УПД, актов и договоров.
    - Формирует «карточку инцидента» (даты, номера, реквизиты сторон).
    - Блокирует процесс, если в чате отсутствуют обязательные документы (например, Акт ТОРГ-2).
    """

    def __init__(self):
        super().__init__("Anna (Secretary)", "Data Extractor and Document Classifier")

    async def run(self, input_data: dict) -> IncidentCard:
        """
        Receives input_data: {"chat_id": int, "file": DocumentInfo, "text": Optional[str]}
        Extracts document type, updates card, checks completeness.
        """
        chat_id = input_data.get("chat_id")
        doc_info: DocumentInfo = input_data.get("file")
        text = input_data.get("text")
        
        # Determine incident card for this chat
        card = IncidentManager.get_or_create_incident(chat_id)

        # Try to infer task from current upload context to avoid irrelevant doc requests.
        self._infer_task_context(card, doc_info, text)
        
        # 1. OCR / Content Extraction
        extracted_content = await self.extract_content(doc_info, text)
        doc_info.content_summary = extracted_content
        
        # 2. Update Card
        card.uploaded_documents.append(doc_info)
        
        # 3. Simple classification logic (Replace with AI later)
        if "TORG-12" in (doc_info.file_name or "").upper():
            logger.info(f"Found TORG-12 in {doc_info.file_name}")
            # Mock extraction logic
            card.contract_number = "123-EXTRACTED" 
        
        IncidentManager.update_incident(chat_id, card)
        return card

    def _infer_task_context(self, card: IncidentCard, doc_info: DocumentInfo, text: Optional[str]) -> None:
        context = f"{doc_info.file_name or ''} {text or ''}".lower()

        contract_markers = ["договор", "согласован", "agreement", "contract", "дс", "спецификац"]
        claim_markers = ["претенз", "рекламац", "брак", "дефект", "торг-2", "torg-2", "торг-12", "torg-12"]

        if any(marker in context for marker in contract_markers):
            card.task_type = "document_drafting"
            if not card.task_description:
                card.task_description = text or f"Согласование договора: {doc_info.file_name}"
        elif any(marker in context for marker in claim_markers) and card.task_type in ["claim", "claim_processing"]:
            card.task_type = "claim_processing"

        card.required_documents = self.get_required_documents(card)

    def get_required_documents(self, card: IncidentCard) -> List[str]:
        """
        Returns context-aware list of required documents.
        """
        if card.task_type in ["document_drafting", "legal_advice", "consultation"]:
            # For contract drafting/review don't ask for warehouse docs like TORG-12 by default.
            return ["Contract"]

        # Default flow for claims/defects.
        return ["TORG-12", "Act-TORG-2", "Contract", "Photos"]

    async def extract_content(self, file_info: DocumentInfo, text_fallback: Optional[str] = None) -> str:
        """
        Placeholder for OCR / Vision extraction.
        For now constructs a description based on filename and user caption.
        """
        description = f"File Name: {file_info.file_name}."
        if text_fallback:
            description += f" User caption/comment: '{text_fallback}'."
        
        # TODO: Implement OCR using Tesseract or OpenAI Vision here
        return description

    def check_completeness(self, card: IncidentCard) -> List[str]:
        """
        Returns missing required documents.
        """
        required_docs = self.get_required_documents(card)
        uploaded_names = [d.file_name.upper() for d in card.uploaded_documents]
        missing = []
        for req in required_docs:
            # Simple substring check (heuristic) - replace with semantic check later
            found = any(req.upper() in name for name in uploaded_names)
            # Special check for 'Contract'
            if req == "Contract" and card.contract_number:
                found = True
            
            if not found:
                missing.append(req)
        return missing
