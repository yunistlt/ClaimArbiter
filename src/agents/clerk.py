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
        logger.info(f"Dmitry (Clerk) drafting document for chat {card.chat_id}")
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are Dmitry, a diligent official correspondence secretary for ZMK. "
                       "Your job is to draft a perfect formal response letter based on internal analysis. "
                       "Use strictly formal Russian business style (high bureaucratic standard). "
                       "If the decision is 'Refusal', be polite but firm. "
                       "If 'Acceptance', confirm next steps clearly. "
                       "Do not use markdown in the letter body, just plain text."),
            ("user", "Technical Verdict: {technical}\n"
                     "Legal Strategy: {legal}\n\n"
                     "Draft the official response letter addressed to the 'General Director'.")
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        
        try:
            draft = await chain.ainvoke({
                "technical": card.technical_verdict,
                "legal": card.legal_strategy
            })
            card.generated_response = draft
        except Exception as e:
            logger.error(f"LLM Error in Clerk: {e}")
            card.generated_response = "Error drafting document."
            
        return card
