import asyncio
import logging
import os

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import TELEGRAM_BOT_TOKEN
from bot.handlers import common, documents

# Configure logging
logging.basicConfig(level=logging.INFO)


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN is not set in .env file")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(documents.router)

    # Web admin panel — runs on port 80 alongside the bot in the same event loop
    web_port = int(os.getenv("WEB_PORT", "80"))
    web_config = uvicorn.Config(
        "web.app:app",
        host="0.0.0.0",
        port=web_port,
        log_level="info",
    )
    web_server = uvicorn.Server(web_config)

    logging.info("Starting bot + web admin panel on port %d...", web_port)
    await asyncio.gather(
        dp.start_polling(bot),
        web_server.serve(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped!")
