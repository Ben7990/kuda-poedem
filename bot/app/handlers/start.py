from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards import main_menu
from app.states import TripForm

router = Router()

WELCOME = (
    "👋 <b>Привет! Я планировщик путешествий.</b>\n\n"
    "Отвечу на 5 вопросов и соберу маршрут по дням:\n"
    "🌍 куда, 🛫 откуда, 📅 даты, 🎯 формат, 💰 бюджет.\n\n"
    "Бонусом — ссылки на билеты, отели и экскурсии."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(WELCOME, reply_markup=main_menu())


@router.callback_query(F.data == "plan:start")
async def on_plan_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await state.set_state(TripForm.destination)
    await callback.message.answer(
        "🌍 <b>Куда хотите поехать?</b>\n\n"
        "Введите страну или город (можно несколько через запятую):\n"
        "например: <i>Кемер, Турция</i>"
    )