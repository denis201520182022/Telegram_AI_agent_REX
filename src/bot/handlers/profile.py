from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from src.database.session import async_session_maker
from src.database.models import User
from src.bot.keyboards.menu import get_main_menu
from src.utils.admin import is_admin
from src.bot.handlers.survey import start_generic_survey, start_coach_survey

# --- OBSERVABILITY ---
from src.utils.logger import logger

router = Router()

# Клавиатура выбора анкеты для редактирования
# Приводим callback_data к формату, который мы сможем легко обработать
edit_menu_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🥗 Изменить режим Коуча", callback_data="edit_mode_coach")],
    [InlineKeyboardButton(text="❤️ Анкета знакомств", callback_data="edit_mode_dating")],
    [InlineKeyboardButton(text="🔮 Данные рождения (Астро)", callback_data="edit_mode_natal")],
    [InlineKeyboardButton(text="↩️ Назад в меню", callback_data="back_to_main_menu")]
])

@router.callback_query(F.data == "edit_profile")
async def edit_profile_menu(callback: CallbackQuery):
    """Показывает меню выбора, какую анкету перезаполнить."""
    logger.info("user_opened_edit_profile", user_id=callback.from_user.id)
    
    await callback.message.edit_text(
        "⚙️ <b>Настройки профиля</b>\n\nКакую анкету вы хотите заполнить заново?",
        reply_markup=edit_menu_kb,
        parse_mode="HTML"
    )
    await callback.answer()

# --- ОБРАБОТЧИКИ ПЕРЕЗАПОЛНЕНИЯ ---

@router.callback_query(F.data == "edit_mode_coach")
async def edit_coach(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Вызываем специальный стартер для коуча (с выбором 🥗/🏋️/🔥)
    await start_coach_survey(callback.message, state)

@router.callback_query(F.data == "edit_mode_dating")
async def edit_dating(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_generic_survey(callback.message, state, mode="dating")

@router.callback_query(F.data == "edit_mode_natal")
async def edit_natal(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_generic_survey(callback.message, state, mode="natal")

# --- ВОЗВРАТ ---

@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery):
    """Возвращает в главное меню с учетом прав и кредитов."""
    user_id = callback.from_user.id
    
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        credits = user.natal_chart_credits if user else 0
        admin_status = is_admin(user_id)

    # Убираем инлайн-сообщение и выводим главное меню с Reply-кнопками
    await callback.message.delete()
    await callback.message.answer(
        "🏠 Главное меню:", 
        reply_markup=get_main_menu(natal_credits=credits, is_admin=admin_status)
    )
    await callback.answer()

# (В handlers/profile.py)
@router.message(F.text == "👤 Мой профиль")
async def profile_entry(message: Message):
    # Тот же код, что и в edit_profile_menu, только ответ на message
    await message.answer("⚙️ Настройки профиля...", reply_markup=edit_menu_kb)