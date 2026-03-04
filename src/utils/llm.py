from langchain_openai import ChatOpenAI
from config import OPENAI_API_KEY, OPENAI_BASE_URL

def get_llm(model_name: str = "gpt-4o"):
    """
    Returns a configured ChatOpenAI instance.
    """
    return ChatOpenAI(
        model=model_name,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0
    )
