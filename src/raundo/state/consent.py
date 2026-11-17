"""FSM markers for consent acceptance and age confirmation."""

from aiogram.fsm.state import State, StatesGroup


class ConsentStates(StatesGroup):
    awaiting_acceptance = State()
    awaiting_age = State()
