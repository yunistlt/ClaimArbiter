from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseAgent(ABC):
    """
    Abstract base class for all specialized agents within the ClaimArbiter system.
    """
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        
    @abstractmethod
    async def run(self, input_data: Any) -> Any:
        """
        Execute the agent's logic.
        
        :param input_data: Input data for the agent to process.
        :return: Result of the agent's processing.
        """
        pass

    def __str__(self):
        return f"Agent: {self.name} - {self.description}"
