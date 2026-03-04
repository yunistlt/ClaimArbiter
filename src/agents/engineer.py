from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from agents.base import BaseAgent
from models import IncidentCard
from utils.llm import get_llm
import logging

logger = logging.getLogger(__name__)

class EngineerAgent(BaseAgent):
    """
    Агент «Инженер-технолог» (Борис Петрович):
    - Сопоставляет дефекты на фото с допусками в ГОСТ и ТУ «ЗМК».
    - Выносит вердикт: является ли случай гарантийным.
    """
    def __init__(self):
        super().__init__("Boris (Engineer)", "Technical Compliance Evaluator")
        self.llm = get_llm("gpt-4o")

    async def run(self, card: IncidentCard) -> IncidentCard:
        logger.info(f"Boris (Engineer) starting analysis for chat {card.chat_id}")
        
        # Collect available information
        context_parts = []
        for doc in card.uploaded_documents:
            name = doc.file_name
            summary = doc.content_summary or "No content extracted"
            context_parts.append(f"Document: {name}\nContent/Description: {summary}")
            
        context_text = "\n---\n".join(context_parts)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are Boris Petrovich, the Chief Technical Engineer at ZMK (Metal Structures Plant). "
                       "You are strict, experienced, and reference specific GOST standards. "
                       "Your task is to analyze claims about product defects. "
                       "Determine if the issue is likely a manufacturing defect (Warranty Case) "
                       "or a result of improper handling/installation (Not Warranty). "
                       "Be grumpy but fair. Use technical jargon (welds, seams, steel grade)."),
            ("user", "Analyze the following evidence:\n\n{context}\n\n"
                     "Provide a technical verdict: Is this a warranty case? Explain why in detail.")
        ])
        
        chain = prompt | self.llm | StrOutputParser()
        
        try:
            verdict = await chain.ainvoke({"context": context_text})
            card.technical_verdict = verdict
        except Exception as e:
            logger.error(f"LLM Error in Engineer: {e}")
            card.technical_verdict = "Error in analysis. Treating as requiring manual review."
            
        return card
