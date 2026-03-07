import json
import logging
import os
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agents.base import BaseAgent
from models import IncidentCard
from utils.llm import get_llm

logger = logging.getLogger(__name__)

class LawyerAgent(BaseAgent):
    """
    Агент «Юрист-аналитик» (Елена Владимировна):
    - Проверяет соблюдение процессуальных сроков.
    - Строит правовую позицию (Сильная/Слабая).
    - Использует данные о компании из data/companies.json.
    """
    def __init__(self):
        super().__init__("Elena (Lawyer)", "Legal Strategist")
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
        logger.info(f"Elena (Lawyer) analyzing case for chat {card.chat_id}. Task: {card.task_type}")
        
        # Decide prompt based on task
        if card.task_type == "claim_processing":
            system_msg = (f"You are Elena Vladimirovna, the Head of Legal Department at ZMK. "
                          f"You have access to the following company details:\n{self.companies_data}\n\n"
                          "You are sharp, strategic, and protective of the company's interests. "
                          "Based on the technical verdict from Boris, formulate a legal strategy. "
                          "Reference relevant articles of the Russian Civil Code (ГК РФ) forcefully "
                          "(e.g., Article 475, 476, 513). Be professional and precise.")
            user_msg = ("Technical Engineer's Verdict:\n{verdict}\n\n"
                        "Develop a legal strategy (Strong/Weak position) and recommendation.")
            input_vars = {"verdict": card.technical_verdict or "No technical verdict provided."}

        else:
            # General legal task
            system_msg = (f"You are Elena Vladimirovna, the Head of Legal Department at ZMK. "
                          f"You have access to the following company details:\n{self.companies_data}\n\n"
                          "You are sharp, strategic, and protective of the company's interests. "
                          "Analyze the user's request and provide a professional legal opinion or strategy. "
                          "If requested to draft a document, use the provided company details (INN, Address, CEO, etc.). "
                          "IMPORTANT: If the user has NOT specified which of your companies is the sender, and you have multiple options, "
                          "your strategy must be to ASK the user to clarify this (e.g., 'Which company is the sender?'). "
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
