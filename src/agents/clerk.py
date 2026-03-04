from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agents.base import BaseAgent
from models import IncidentCard
from utils.llm import get_llm
import logging

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

    async def run(self, card: IncidentCard) -> IncidentCard:
        logger.info(f"Dmitry (Clerk) drafting document for chat {card.chat_id}. Task: {card.task_type}")

        # Choose prompt logic
        if card.task_type == "claim_processing":
            system_msg = ("You are Dmitry, a diligent official correspondence secretary for ZMK. "
                          "Your job is to draft a perfect formal response letter based on internal analysis. "
                          "Use strictly formal Russian business style (high bureaucratic standard). "
                          "Synthesize the Technical Verdict and Legal Strategy into one document.")
            user_msg = ("Technical Verdict: {technical}\n"
                        "Legal Strategy: {legal}\n\n"
                        "Draft the official response letter addressed to the 'General Director'. "
                        "Do not explain your work, just output the document text.")
            input_vars = {
                "technical": card.technical_verdict or "Not provided (check if relevant)",
                "legal": card.legal_strategy or "Not provided"
            }
        else:
            # General document drafting based on Legal Strategy / User Request
            system_msg = ("You are Dmitry, a senior document controller and drafter for ZMK Legal Dept. "
                          "Your job is to draft a high-quality legal document (contract, letter, claim, suit, memo) "
                          "based on the instructions provided by the Head of Legal (Elena). "
                          "Use strictly formal Russian business/legal style. "
                          "Ensure all standard clauses (Force Majeure, Dispute Resolution) are implied or included if relevant.")
            user_msg = ("Task Description: {description}\n"
                        "Legal Instructions / Strategy: {legal}\n\n"
                        "Draft the requested document in full formal text. "
                        "Do not explain your work, just output the document body.")
            input_vars = {
                "description": card.task_description or "Draft a document as requested.",
                "legal": card.legal_strategy or "Follow standard legal practice."
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
            card.generated_response = "Error drafting document."
            
        return card
