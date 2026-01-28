from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_dating_kb(target_user_id: int) -> InlineKeyboardMarkup:
    """
    Кнопки под анкетой кандидата, которую присылает бот.
    В callback_data зашивается ID пользователя, которого оценивают.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❤️ Лайк", callback_data=f"like_{target_user_id}"),
            InlineKeyboardButton(text="👎 Пропустить", callback_data=f"dislike_{target_user_id}")
        ],
        # [
        #     # Кнопка жалобы (можно будет добавить позже, если понадобится модерация)
        #     InlineKeyboardButton(text="⚠️ Пожаловаться", callback_data=f"report_{target_user_id}")
        # ]
    ])

def get_contact_kb(username: str | None) -> InlineKeyboardMarkup:
    """
    Кнопка для связи с пользователем после взаимного лайка (мэтча).
    """
    # Если у пользователя нет username, кнопка будет неактивна, но пользователь получит уведомление
    # и сможет найти партнера по имени/упоминанию.
    is_active = bool(username)
    url = f"https://t.me/{username}" if is_active else "https://t.me/"
    
    button_text = "💬 Написать партнеру" if is_active else "🔒 У пользователя скрыт профиль"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button_text, url=url)]
    ])


def get_dating_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
             
            [KeyboardButton(text="📝 Изменить анкету"), KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Режим знакомств"
    )