import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Список разрешенных пользователей (ID через запятую)
# Пример: 12345678, 87654321
raw_allowed_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = [int(id.strip()) for id in raw_allowed_ids.split(",") if id.strip().isdigit()]
