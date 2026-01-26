from aiogram.fsm.state import StatesGroup, State

class SurveyState(StatesGroup):
    choosing_mode = State()      # Выбор: Питание / Спорт / Всё
    in_progress = State()        # Отвечает на вопросы
    confirmation = State()       # Подтверждает ответы
    policy_acceptance = State()  # Согласие с политикой
    choosing_timezone = State()  # Выбор города
    choosing_time = State()      # Выбор времени трекинга

class CoachState(StatesGroup):
    chatting = State()           # Основной режим: общение с ИИ