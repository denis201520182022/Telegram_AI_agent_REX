from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_coach_menu() -> ReplyKeyboardMarkup:
    """
    Главное меню в режиме общения с ИИ-коучем.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Настройки режима")],
            [KeyboardButton(text="🔙 Выйти в главное меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Спросите что-нибудь у коуча..."
    )

def get_coach_settings_kb(is_tracking: bool) -> InlineKeyboardMarkup:
    """
    Инлайн-кнопки для меню настроек коуча.
    """
    track_text = "🔔 Выключить трекинг" if is_tracking else "🔕 Включить трекинг"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=track_text, callback_data="toggle_tracking")],
        [InlineKeyboardButton(text="🔄 Сменить формат (Питание/Спорт)", callback_data="change_coach_mode")],
    ])

def get_change_format_choice_kb() -> InlineKeyboardMarkup:
    """
    Кнопки выбора: адаптировать существующий план или пройти опрос заново.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪄 Адаптировать текущий план", callback_data="coach_adapt")],
        [InlineKeyboardButton(text="🆕 Заполнить анкету заново", callback_data="coach_reset")]
    ])