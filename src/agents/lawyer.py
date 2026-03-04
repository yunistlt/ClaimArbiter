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
        logger.info(f"Elena (Lawyer) analyzing case for chat {card.chat_id}. Task: {card.task_type}")
        
        # Decide prompt based on task
        if card.task_type == "claim_processing":
            system_msg = ("You are Elena Vladimirovna, the Head of Legal Department at ZMK. "
                          "You are sharp, strategic, and protective of the company's interests. "
                          "Based on the technical verdict from Boris, formulate a legal strategy. "
                          "Reference relevant articles of the Russian Civil Code (ГК РФ) forcefully "
                          "(e.g., Article 475, 476, 513). Be professional and precise.")
            user_msg = ("Technical Engineer's Verdict:\n{verdict}\n\n"
                        "Develop a legal strategy (Strong/Weak position) and recommendation.")
            input_vars = {"verdict": card.technical_verdict or "No technical verdict provided."}

        else:
            # General legal task
            system_msg = ("You are Elena Vladimirovna, the Head of Legal Department at ZMK. "
                          "You are sharp, strategic, and protective of the company's interests. "
                          "Analyze the user's request and provide a professional legal opinion or strategy. "
                          "If requested to draft a document, outline the key points and clauses required. "
                          "Reference relevant Russian laws (GK RF, TK RF, etc.).")
            user_msg = ("User Request / Task Description:\n{description}\n\n"
                        "Provide a legal opinion or strategy for this task.")
            input_vars = {"description": card.task_description or "No description provided."}

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_msg),
            ("user", user_msg)
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        
        try:
            strategy = await chain.ainvoke(input_vars)
            card.legal_strategy = strategy
        except Exception as e:
            logger.error(f"LLM Error in Lawyer: {e}")
            card.legal_strategy = "Error generating legal strategy."
            
        return card
