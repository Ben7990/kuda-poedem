import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers import payments, planning, start

logging.basicConfig(level=logging.INFO)

POLL_RETRIES = 6


async def _poll(bot: Bot, dp: Dispatcher) -> None:
    """Long polling с повтором при 409 (старый процесс ещё держит сессию)."""
    for attempt in range(POLL_RETRIES):
        try:
            await dp.start_polling(bot)
            return
        except TelegramConflictError:
            wait = 30 * (attempt + 1)
            logging.warning(
                "Telegram 409: другой экземпляр бота ещё держит сессию, повтор через %s с", wait
            )
            await asyncio.sleep(wait)
    raise SystemExit("Не удалось начать polling: сессия занята другим процессом")


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните.")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(start.router)
    dp.include_router(planning.router)
    dp.include_router(payments.router)

    logging.info("Бот запущен. LLM: %s", "включен" if settings.llm_enabled else "демо-режим")
    await _poll(bot, dp)


if __name__ == "__main__":
    asyncio.run(main())