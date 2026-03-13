import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Supabase (optional). When not configured, app keeps using local storage only.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)

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

# Если включено, пользователь в личке будет автоматически добавлен в allowlist
# при первом обращении. Значения: 1/true/yes/on.
AUTO_ALLOW_PRIVATE_USERS = os.getenv("AUTO_ALLOW_PRIVATE_USERS", "1").strip().lower() in [
    "1", "true", "yes", "on"
]

# Строгое языковое правило: все пользовательские ответы и интерфейсы должны быть на русском языке.
STRICT_RUSSIAN_ONLY = os.getenv("STRICT_RUSSIAN_ONLY", "1").strip().lower() in [
	"1", "true", "yes", "on"
]
