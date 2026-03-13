import json
import logging
import os

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agents.base import BaseAgent
from models import IncidentCard
from utils.llm import get_llm

logger = logging.getLogger(__name__)

class ClerkAgent(BaseAgent):
    """
    Агент «Документовед» (Дмитрий):
    - Сборка выводов всех агентов в итоговый документ.
    - Uses clear, formal Russian business language.
    """
    def __init__(self):
        super().__init__("Dmitry (Clerk)", "Document Drafter")
        self.llm = get_llm("gpt-4o")
        self.companies_data = self._load_company_data()

    def _load_company_data(self) -> str:
        """Загружает информацию о компании из файла."""
        try:
            path = os.path.join(os.getcwd(), "data", "companies.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return json.dumps(data, ensure_ascii=False, indent=2)
            else:
                return "No company data available."
        except Exception as e:
            logger.error(f"Error loading company data: {e}")
            return "Error loading company data."

    async def run(self, card: IncidentCard) -> IncidentCard:
        logger.info(f"Dmitry (Clerk) drafting document for chat {card.chat_id}. Task: {card.task_type}")
        shared_company_block = "You have access to the following company details:\n{companies_data}\n\n"

        # Choose prompt logic
        if card.task_type == "claim_processing":
            system_msg = (f"You are Dmitry, a diligent official correspondence secretary. "
                          f"{shared_company_block}"
                          "Your job is to draft a perfect formal response letter based on internal analysis. "
                          "Unless specified otherwise, use the primary company relevant to the context. "
                          "Use official Russian business style that is clear and readable, without excessive bureaucracy. "
                          "Synthesize the Technical Verdict and Legal Strategy into one document.")
            user_msg = ("Technical Verdict: {technical}\n"
                        "Legal Strategy: {legal}\n\n"
                        "Draft the official response letter addressed to the 'General Director'. "
                        "Do not explain your work, just output the document text.")
            input_vars = {
                "technical": card.technical_verdict or "Not provided (check if relevant)",
                "legal": card.legal_strategy or "Not provided",
                "companies_data": self.companies_data,
            }
        else:
            # General document drafting based on Legal Strategy / User Request
            system_msg = (f"You are Dmitry, a senior document controller and drafter. "
                          f"{shared_company_block}"
                          "Your job is to draft a high-quality legal document (contract, letter, claim, suit, memo) "
                          "based on the instructions provided by the Head of Legal (Elena). "
                          "Identify which of our companies is the Sender/Claimant from the legal instructions. "
                          "If not specified, assume the user will fill it in, but prefer using available details if context implies one. "
                          "Use official Russian business/legal style that is concise and readable. "
                          "Ensure all standard clauses (Force Majeure, Dispute Resolution) are implied or included if relevant.")
            user_msg = ("Task Description: {description}\n"
                        "Legal Instructions / Strategy: {legal}\n\n"
                        "Draft the requested document in full formal text. "
                        "Do not explain your work, just output the document body. "
                        "Include the specific company header (Name, INN, Address, Bank Details) if known. "
                        "If the Legal Strategy says to ASK for company details, output ONLY the question to the user.")
            input_vars = {
                "description": card.task_description or "Draft a document as requested.",
                "legal": card.legal_strategy or "Follow standard legal practice.",
                "companies_data": self.companies_data,
            }
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_msg),
            ("user", user_msg)
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        
        try:
            draft = await chain.ainvoke(input_vars)
            card.generated_response = draft
        except Exception as e:
            logger.error(f"LLM Error in Clerk: {e}")
            # Fallback: return a meaningful draft from legal strategy instead of a raw error.
            fallback_parts = [
                "Не удалось сгенерировать полный документ автоматически.",
                "Ниже проект на основе текущей правовой позиции:",
                "",
                card.legal_strategy or "Правовая позиция пока не сформирована.",
            ]
            card.generated_response = "\n".join(fallback_parts)
            
        return card
