"""FSM states for investor wizard."""

from aiogram.fsm.state import State, StatesGroup


class InvestorWizard(StatesGroup):
    hub = State()
    display_name = State()
    investor_type = State()
    support_modes = State()
    organization = State()
    position = State()
    strategy = State()
    sectors = State()
    stages = State()
    check_size = State()
    geography = State()
    public_link = State()
    photo = State()
    contact_policy = State()
    preview = State()
    premium_offer = State()
    edit_pick = State()
    manage = State()
    delete_confirm = State()
