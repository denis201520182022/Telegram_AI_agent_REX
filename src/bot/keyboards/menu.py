from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_menu(natal_credits: int = 0, is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Главное меню бота.
    Генерируется динамически в зависимости от прав админа и бонусов пользователя.
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
    
    # Ряд 3: Натальная карта (показываем, если есть попытки или это админ)
    row3 = []
    if natal_credits > 0 or is_admin:
        label = "🌟 Натальная карта"
        if not is_admin:
            label += f" ({natal_credits})"
        row3.append(KeyboardButton(text=label))

    # Ряд 4: Профиль и Справка
    row4 = [
        KeyboardButton(text="👤 Мой профиль"),
        KeyboardButton(text="ℹ️ Справка")
    ]
    
    # Ряд 5: Админка
    row5 = []
    if is_admin:
        row5.append(KeyboardButton(text="🔒 Админка"))

    keyboard = [row1, row2]
    if row3: 
        keyboard.append(row3)
    keyboard.append(row4)
    if row5:
        keyboard.append(row5)

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите режим 👇"
    )