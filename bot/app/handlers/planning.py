from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.keyboards import formats_menu, offer_menu, result_menu
from app.render import render_plan
from app.services.billing import send_invoice
from app.services.planner import build_plan, parse_budget, parse_dates
from app.services.prices import canonicalize_place
from app.states import TripForm

router = Router()

CANCEL_TEXT = "🔁 Нажмите /start, чтобы начать заново."
BACK_TEXT = "Выполните ввод заново."


async def _cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(CANCEL_TEXT)


def _prompt_origin():
    return "🛫 <b>Откуда удобно вылетать?</b>\n\nВведите город отправления:\nнапример: <i>Ростов-на-Дону</i>"


def _prompt_dates():
    return (
        "📅 <b>Даты поездки?</b>\n\n"
        "Введите период в формате:\n"
        "<i>10.11.2026 - 20.11.2026</i>"
    )


def _prompt_format():
    return "🎯 <b>Формат путешествия?</b>"


def _prompt_budget():
    return "💰 <b>Бюджет поездки?</b>\n\nВведите сумму в рублях (числом):\nнапример: <i>180000</i>"


@router.message(TripForm.destination)
async def ask_origin(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text:
        await message.answer("Введите название страны или города.")
        return
    fixed = await canonicalize_place(text)
    await state.update_data(destination=fixed)
    if fixed != text:
        await message.answer(f"📍 Принято: <b>{fixed}</b>")
    await state.set_state(TripForm.origin)
    await message.answer(_prompt_origin())


@router.message(TripForm.origin)
async def ask_dates(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text:
        await message.answer("Введите город отправления.")
        return
    fixed = await canonicalize_place(text)
    await state.update_data(origin=fixed)
    if fixed != text:
        await message.answer(f"📍 Принято: <b>{fixed}</b>")
    await state.set_state(TripForm.dates)
    await message.answer(_prompt_dates())


@router.message(TripForm.dates)
async def ask_format(message: Message, state: FSMContext) -> None:
    parsed = parse_dates(message.text)
    if not parsed:
        await message.answer(
            f"Не могу распознать даты. Пример: <i>10.11.2026 - 20.11.2026</i>\n\n{BACK_TEXT}"
        )
        return
    await state.update_data(date_start=parsed[0], date_end=parsed[1])
    await state.set_state(TripForm.trip_format)
    await message.answer(_prompt_format(), reply_markup=formats_menu())


@router.callback_query(TripForm.trip_format, F.data.startswith("fmt:"))
async def on_format(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    key = callback.data.split(":", 1)[1]
    if key == "custom":
        await state.set_state(TripForm.custom_format)
        await callback.message.answer("✍️ Опишите формат одним-двумя словами.")
        return
    await state.update_data(format=key)
    await state.set_state(TripForm.budget)
    await callback.message.answer(_prompt_budget())


@router.message(TripForm.custom_format)
async def on_custom_format(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text:
        await message.answer("Опишите формат поездки.")
        return
    await state.update_data(format=text)
    await state.set_state(TripForm.budget)
    await message.answer(_prompt_budget())


@router.message(TripForm.budget)
async def ask_offer(message: Message, state: FSMContext) -> None:
    budget = parse_budget(message.text)
    if not budget:
        await message.answer(f"Введите сумму числом, например: <i>180000</i>\n\n{BACK_TEXT}")
        return
    await state.update_data(budget_rub=budget, travelers=2)
    await state.set_state(TripForm.offer)
    await message.answer(
        f"🎯 <b>Всё, что нужно, собрано!</b>\n\n"
        f"Полный персональный маршрут по дням — <b>{settings.price_per_trip_rub} ₽</b>\n\n"
        f"В полном плане: логистика с пересадками, реальные рейсы и поезда с временем, "
        f"отели, экскурсии, бюджет по дням, чек-лист и все ссылки.\n\n"
        f"💡 В демо покажу такой же полный маршрут, но <b>другого направления</b> — "
        f"как образец.",
        reply_markup=offer_menu(settings.price_per_trip_rub),
    )


async def _prepare_plan(message: Message, state: FSMContext, demo: bool = False) -> None:
    data = await state.get_data()
    status = await message.answer("⏳ Составляю маршрут… Пару секунд.")
    try:
        plan = await build_plan(data, demo=demo)
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"⚠️ Не удалось составить маршрут. Попробуйте ещё раз.\n\n{exc}")
        return
    finally:
        await state.clear()

    for chunk in render_plan(plan):
        await message.answer(chunk)
    await message.answer("Готово! 🔗 Поделитесь или постройте новый маршрут.", reply_markup=result_menu())
    await status.delete()


@router.callback_query(TripForm.offer, F.data == "offer:preview")
async def on_preview(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Показываю демо-план")
    await _prepare_plan(callback.message, state, demo=True)


@router.callback_query(TripForm.offer, F.data == "offer:pay")
async def on_pay(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    if not settings.payments_enabled:
        await callback.message.answer("💳 Настраиваю оплату… Пока покажу полный маршрут бесплатно.")
        await _prepare_plan(callback.message, state, demo=False)
        return
    await state.set_state(TripForm.paid)
    try:
        await send_invoice(bot, callback.message.chat.id)
    except Exception as exc:  # noqa: BLE001
        await callback.message.answer(f"⚠️ Не удалось выставить счёт: {exc}\n\nДля оплаты включите омнибус-платежи ЮKassa через @BotFather.")
        await _prepare_plan(callback.message, state, demo=False)