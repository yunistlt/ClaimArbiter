import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import TELEGRAM_BOT_TOKEN
from bot.handlers import common, documents

# Configure logging
logging.basicConfig(level=logging.INFO)

async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN is not set in .env file")
        return

    # Initialize Bot instance with default bot properties which will be passed to all API calls
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    # All handlers should be attached to the Router (or Dispatcher)
    dp = Dispatcher()
    
    dp.include_router(common.router)
    dp.include_router(documents.router)

    # And the run events dispatching
    logging.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped!")
