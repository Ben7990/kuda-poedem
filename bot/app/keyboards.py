from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

FORMATS = [
    ("business", "💼 Деловая поездка"),
    ("blogger", "📸 Тревел-блогерство"),
    ("beach", "🏖 Отдых у моря"),
    ("culture", "🏛 Культура и экскурсии"),
    ("extreme", "🪂 Экстрим"),
    ("custom", "✍️ Свой вариант"),
]


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗺 Спланировать поездку", callback_data="plan:start")],
        ]
    )


def formats_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"fmt:{key}")]
        for key, title in FORMATS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Новый маршрут", callback_data="plan:start")],
        ]
    )


def offer_menu(price: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price} ₽ и получить полный маршрут", callback_data="offer:pay")],
            [InlineKeyboardButton(text="🆓 Бесплатный демо-план", callback_data="offer:preview")],
        ]
    )
