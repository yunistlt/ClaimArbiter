from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

class DocumentInfo(BaseModel):
    file_id: str
    file_name: str
    file_type: str  # 'pdf', 'image', etc.
    upload_date: datetime = Field(default_factory=datetime.now)
    content_summary: Optional[str] = None  # Text extracted or summary

class ChatMessage(BaseModel):
    role: str # 'user' or 'bot'
    content: str
    username: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)

class IncidentCard(BaseModel):
    """
    Карточка инцидента. Хранит все данные по текущей рекламации в чате.
    """
    chat_id: int
    status: str = "init"  # init, collecting_evidence, analyzing, drafting, done
    
    # Context History
    chat_history: List[ChatMessage] = Field(default_factory=list)
    
    # Extracted Data
    contract_number: Optional[str] = None
    contract_date: Optional[str] = None
    buyer_name: Optional[str] = None
    
    # Documents
    required_documents: List[str] = ["TORG-12", "Act-TORG-2", "Contract", "Photos"]
    uploaded_documents: List[DocumentInfo] = Field(default_factory=list)
    
    # Analysis
    technical_verdict: Optional[str] = None
    legal_strategy: Optional[str] = None
    generated_response: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True
