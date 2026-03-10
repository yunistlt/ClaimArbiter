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

# Пользователи, которые могут вручную проверять/выпускать документы.
# Если не задано явно, используется ALLOWED_USER_IDS.
raw_reviewer_ids = os.getenv("LAWYER_REVIEWER_IDS", "")
LAWYER_REVIEWER_IDS = [int(id.strip()) for id in raw_reviewer_ids.split(",") if id.strip().isdigit()]
if not LAWYER_REVIEWER_IDS:
	LAWYER_REVIEWER_IDS = ALLOWED_USER_IDS
