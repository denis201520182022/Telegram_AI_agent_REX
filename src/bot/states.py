from aiogram.fsm.state import StatesGroup, State

class SurveyState(StatesGroup):
    """
    Состояния для процесса заполнения анкет. 
    Используется для Коуча, Дейтинга и Натальной карты.
    """
    choosing_mode = State()      # Выбор под-режима Коуча (🥗 / 🏋️ / 🔥)
    in_progress = State()        # Активная фаза ответа на вопросы из Google Sheets
    confirmation = State()       # Проверка ответов пользователем (Подтвердить / Заново)
    
    # Специфичные шаги после первой анкеты (Коуч)
    policy_acceptance = State()  # Однократное принятие политики обработки данных
    choosing_timezone = State()  # Выбор города/таймзоны для корректного времени трекинга
    choosing_time = State()      # Выбор конкретного часа для уведомлений от ИИ

class CoachState(StatesGroup):
    """
    Состояния для режима активного коучинга (после генерации плана).
    """
    chatting = State()           # Свободное общение с ИИ, сохранение контекста и трекинг