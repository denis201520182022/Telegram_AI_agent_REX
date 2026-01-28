from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_astro_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✨ Получить прогноз на сегодня")],
            [KeyboardButton(text="🔄 Изменить данные"), KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Режим Астролога"
    )