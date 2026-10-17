"""Обработка Telegram Payments: подтверждение и выдача после оплаты."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, PreCheckoutQuery

from app.states import TripForm

logger = logging.getLogger(__name__)

router = Router()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    if query.invoice_payload == "access:full_itinerary":
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неизвестный товар. Начните заново /start")


@router.message(F.successful_payment)
async def on_paid(message: Message, state: FSMContext) -> None:
    logger.info("Оплата получена: %s %s (%s)", message.successful_payment.currency,
                message.successful_payment.total_amount, message.successful_payment.invoice_payload)
    await message.answer("✅ Оплата прошла! Собираю ваш полный маршрут…")
    from app.handlers.planning import _prepare_plan

    await _prepare_plan(message, state, demo=False)