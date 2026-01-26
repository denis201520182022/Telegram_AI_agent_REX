from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_coach_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мой план"), KeyboardButton(text="⚙️ Настройки режима")],
            [KeyboardButton(text="🔙 Выйти в главное меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Общайтесь с коучем..."
    )