from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agents.base import BaseAgent
from models import IncidentCard
from utils.llm import get_llm
import logging

logger = logging.getLogger(__name__)

class LawyerAgent(BaseAgent):
    """
    Агент «Юрист-аналитик» (Елена Владимировна):
    - Проверяет соблюдение процессуальных сроков.
    - Строит правовую позицию (Сильная/Слабая).
    """
    def __init__(self):
        super().__init__("Elena (Lawyer)", "Legal Strategist")
        self.llm = get_llm("gpt-4o")

    async def run(self, card: IncidentCard) -> IncidentCard:
        logger.info(f"Elena (Lawyer) analyzing case for chat {card.chat_id}")
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are Elena Vladimirovna, the Head of Legal Department at ZMK. "
                       "You are sharp, strategic, and protective of the company's interests. "
                       "Based on the technical verdict from Boris, formulate a legal strategy. "
                       "Reference relevant articles of the Russian Civil Code (ГК РФ) forcefully "
                       "(e.g., Article 475, 476, 513). Be professional and precise."),
            ("user", "Technical Engineer's Verdict:\n{verdict}\n\n"
                     "Develop a legal strategy (Strong/Weak position) and recommendation.")
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        
        try:
            strategy = await chain.ainvoke({"verdict": card.technical_verdict or "No verdict provided."})
            card.legal_strategy = strategy
        except Exception as e:
            logger.error(f"LLM Error in Lawyer: {e}")
            card.legal_strategy = "Error generating legal strategy."
            
        return card
