"""Биллинг: Telegram Payments invoice."""

from aiogram import Bot
from aiogram.types import LabeledPrice

from app.config import settings

PAYMENT_PAYLOAD = "access:full_itinerary"

TITLE = "Полный маршрут по вашей поездке"
DESCRIPTION = (
    "Разблокирует детальный план по дням со ссылками на билеты, "
    "отели и экскурсии."
)


async def send_invoice(bot: Bot, chat_id: int) -> None:
    await bot.send_invoice(
        chat_id=chat_id,
        title=TITLE,
        description=DESCRIPTION,
        payload=PAYMENT_PAYLOAD,
        provider_token=settings.payments_provider_token,
        currency="RUB",
        prices=[LabeledPrice(label="Маршрут", amount=settings.price_per_trip_rub * 100)],
    )