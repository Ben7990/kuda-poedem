import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers import payments, planning, start

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните.")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(planning.router)
    dp.include_router(payments.router)

    logging.info("Бот запущен. LLM: %s", "включен" if settings.llm_enabled else "демо-режим")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())