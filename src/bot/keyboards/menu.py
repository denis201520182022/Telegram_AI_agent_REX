from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_menu(natal_credits: int = 0, is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Главное меню бота.
    """
    # Ряд 1: Главный сервис
    row1 = [
        KeyboardButton(text="🧠 AI Коуч (Питание/Спорт)")
    ]
    
    # Ряд 2: Доп. сервисы
    row2 = [
        KeyboardButton(text="❤️ Найти партнера"),
        KeyboardButton(text="🔮 Астро-прогноз")
    ]
    
    # Ряд 3: Натальная карта (если есть попытки)
    row3 = []
    if natal_credits > 0:
        row3.append(KeyboardButton(text=f"🌟 Натальная карта ({natal_credits})"))

    # Ряд 4: Сервисные
    row4 = [KeyboardButton(text="ℹ️ Справка")]
    
    if is_admin:
        row4.append(KeyboardButton(text="🔒 Админка"))

    keyboard = [row1, row2]
    if row3: keyboard.append(row3)
    keyboard.append(row4)

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите режим 👇"
    )

# Оставляем кнопку отмены
def get_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Прервать", callback_data="cancel_survey")]
    ])