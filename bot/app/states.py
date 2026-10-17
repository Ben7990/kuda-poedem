from aiogram.fsm.state import State, StatesGroup


class TripForm(StatesGroup):
    destination = State()
    origin = State()
    dates = State()
    trip_format = State()
    custom_format = State()
    budget = State()
    offer = State()
    paid = State()
