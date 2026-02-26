from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_coach_menu() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="📋 Мой план")], # Новый первый ряд
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🏠 Выйти")]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Напиши мне что-нибудь..."
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