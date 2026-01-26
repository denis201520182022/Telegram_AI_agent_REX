from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, desc

from src.bot.states import CoachState
from src.database.session import async_session_maker
from src.database.models import ChatMessage, UserSurvey
from src.services.rabbit import send_to_queue
from src.bot.handlers.survey import start_coach_survey # Для перезапуска

router = Router()

# --- КЛАВИАТУРА МЕНЮ КОУЧА ---
def get_coach_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мой план"), KeyboardButton(text="⚙️ Настройки режима")],
            [KeyboardButton(text="🔙 Выйти в главное меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Общайтесь с коучем..."
    )

# --- ХЕНДЛЕРЫ ---

@router.message(CoachState.chatting, F.text == "📋 Мой план")
async def show_current_plan(message: Message):
    """Показывает текущий сохраненный план."""
    user_id = message.from_user.id
    async with async_session_maker() as session:
        # Ищем последнюю анкету
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at))
            .limit(1)
        )
        
        if survey and survey.current_plan:
            # Телеграм имеет лимит 4096 символов, план может быть длинным.
            # Если очень длинный — лучше разбить, но пока просто обрежем если что.
            plan_text = survey.current_plan[:4000]
            await message.answer(f"📄 <b>Ваш текущий план:</b>\n\n{plan_text}", parse_mode="HTML")
        else:
            await message.answer("⚠️ План еще не сформирован или не найден.")

@router.message(CoachState.chatting, F.text == "⚙️ Настройки режима")
async def change_coach_settings(message: Message, state: FSMContext):
    """Позволяет перезапустить анкету."""
    # Тут можно сделать сложную логику (Адаптировать/Сбросить), 
    # но для MVP сделаем просто перезапуск.
    await message.answer("🔄 Давайте обновим ваши данные.")
    # Импортируем функцию старта анкеты из survey.py
    await start_coach_survey(message, state)

@router.message(CoachState.chatting, F.text == "🔙 Выйти в главное меню")
async def exit_coach_mode(message: Message, state: FSMContext):
    """Выход в главное меню."""
    await state.clear()
    # Импорт внутри функции, чтобы избежать циклического импорта (если menu.py будет импортировать coach.py)
    from src.bot.keyboards.menu import get_main_menu
    # Нужно получить данные для меню (кредиты и тд), но пока упростим
    await message.answer("🏠 Вы вышли в главное меню.", reply_markup=get_main_menu())

@router.message(CoachState.chatting)
async def process_chat_message(message: Message):
    """
    Основной хендлер чата.
    Ловит любой текст (кроме кнопок выше).
    1. Сохраняет сообщение юзера в БД.
    2. Отправляет задачу в AI Worker.
    """
    user_id = message.from_user.id
    text = message.text
    
    if not text: return 

    # 1. Сохраняем сообщение юзера
    async with async_session_maker() as session:
        msg = ChatMessage(user_id=user_id, role='user', content=text)
        session.add(msg)
        await session.commit()
    
    # 2. Отправляем в очередь (имитация набора текста ботом)
    await message.bot.send_chat_action(chat_id=user_id, action="typing")
    
    task = {
        "user_id": user_id,
        "mode": "chat",
        "text": text
    }
    await send_to_queue("q_ai_generation", task)
    
    # Не отвечаем тут, ответ придет от Sender Worker через уведомление