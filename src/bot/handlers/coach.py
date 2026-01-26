from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, desc

from src.bot.states import CoachState
from src.database.session import async_session_maker
from src.database.models import ChatMessage, UserSurvey
from src.services.rabbit import send_to_queue
from src.bot.handlers.survey import start_coach_survey 
# ИМПОРТИРУЕМ КЛАВИАТУРУ ИЗ НОВОГО ФАЙЛА:
from src.bot.keyboards.coach import get_coach_menu 

router = Router()

# --- ХЕНДЛЕРЫ ---

@router.message(CoachState.chatting, F.text == "📋 Мой план")
async def show_current_plan(message: Message):
    """Показывает текущий сохраненный план."""
    user_id = message.from_user.id
    async with async_session_maker() as session:
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at))
            .limit(1)
        )
        
        if survey and survey.current_plan:
            plan_text = survey.current_plan[:4000]
            await message.answer(f"📄 <b>Ваш текущий план:</b>\n\n{plan_text}", parse_mode="HTML")
        else:
            await message.answer("⚠️ План еще не сформирован или не найден.")

@router.message(CoachState.chatting, F.text == "⚙️ Настройки режима")
async def change_coach_settings(message: Message, state: FSMContext):
    """Позволяет перезапустить анкету."""
    await message.answer("🔄 Давайте обновим ваши данные.")
    await start_coach_survey(message, state)

@router.message(CoachState.chatting, F.text == "🔙 Выйти в главное меню")
async def exit_coach_mode(message: Message, state: FSMContext):
    """Выход в главное меню."""
    await state.clear()
    # Импорт внутри функции во избежание циклического импорта
    from src.bot.keyboards.menu import get_main_menu
    # Для простоты пока без аргументов
    await message.answer("🏠 Вы вышли в главное меню.", reply_markup=get_main_menu())

@router.message(CoachState.chatting)
async def process_chat_message(message: Message):
    """
    Основной хендлер чата.
    """
    user_id = message.from_user.id
    text = message.text
    
    if not text: return 

    # 1. Сохраняем сообщение юзера
    async with async_session_maker() as session:
        msg = ChatMessage(user_id=user_id, role='user', content=text)
        session.add(msg)
        await session.commit()
    
    # 2. Отправляем в очередь
    await message.bot.send_chat_action(chat_id=user_id, action="typing")
    
    task = {
        "user_id": user_id,
        "mode": "chat",
        "text": text
    }
    await send_to_queue("q_ai_generation", task)