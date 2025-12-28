from aiogram.fsm.state import StatesGroup, State


class AuthStates(StatesGroup):
    provider = State()
    login = State()
    password = State()


class AIStates(StatesGroup):
    waiting_input = State()


class WrappedState(StatesGroup):
    waiting_for_style = State()


class SupportStates(StatesGroup):
    waiting_message = State()
