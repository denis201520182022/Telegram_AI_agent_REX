from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# 1. Выбор режима Коуча
def get_coach_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥗 Питание", callback_data="mode_nutrition")],
        [InlineKeyboardButton(text="🏋️ Тренировки", callback_data="mode_workout")],
        [InlineKeyboardButton(text="🔥 Всё вместе (Питание + Спорт)", callback_data="mode_combined")]
    ])

# 2. Подтверждение анкеты
def get_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Всё верно, создаем план!", callback_data="survey_confirm")],
        [InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="survey_restart")]
    ])

# 3. Согласие с политикой
def get_policy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласен(а)", callback_data="policy_accept")],
        [InlineKeyboardButton(text="❌ Отказаться", callback_data="policy_decline")]
    ])

# 4. Выбор Таймзоны (Крупные города)
def get_timezone_kb() -> InlineKeyboardMarkup:
    cities = [
        ("Калининград (UTC+2)", "Europe/Kaliningrad"),
        ("Москва / Питер (UTC+3)", "Europe/Moscow"),
        ("Самара (UTC+4)", "Europe/Samara"),
        ("Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
        ("Омск (UTC+6)", "Asia/Omsk"),
        ("Новосибирск (UTC+7)", "Asia/Novosibirsk"),
        ("Иркутск (UTC+8)", "Asia/Irkutsk"),
        ("Якутск (UTC+9)", "Asia/Yakutsk"),
        ("Владивосток (UTC+10)", "Asia/Vladivostok"),
        ("Магадан (UTC+11)", "Asia/Magadan"),
        ("Камчатка (UTC+12)", "Asia/Kamchatka"),
    ]
    
    kb = []
    for name, tz_key in cities:
        # callback_data лимит 64 байта, tz_key влезает
        kb.append([InlineKeyboardButton(text=name, callback_data=f"tz_{tz_key}")])
        
    return InlineKeyboardMarkup(inline_keyboard=kb)

# 5. Выбор времени трекинга
def get_tracking_time_kb() -> InlineKeyboardMarkup:
    times = ["09:00", "12:00", "15:00", "18:00", "20:00", "21:00", "22:00"]
    row = []
    keyboard = []
    
    for t in times:
        row.append(InlineKeyboardButton(text=t, callback_data=f"time_{t}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# 6. Кнопка отмены (в процессе анкеты)
def get_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Прервать", callback_data="cancel_survey")]
    ])