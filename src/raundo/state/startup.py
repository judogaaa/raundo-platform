"""FSM states for founder startup wizard."""

from aiogram.fsm.state import State, StatesGroup


class StartupWizard(StatesGroup):
    hub = State()
    name = State()
    pitch = State()
    description = State()
    primary_sector = State()
    secondary_sectors = State()
    stage = State()
    links = State()
    photos = State()
    intents = State()
    audience = State()
    audience_note = State()
    progress = State()
    monetization = State()
    monetization_note = State()
    investment_ask = State()
    fundraising = State()
    use_of_funds = State()
    geography = State()
    founder_info = State()
    contact_policy = State()
    preview = State()
    premium_offer = State()
    edit_pick = State()
    manage = State()
    delete_confirm = State()
