from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from src.database.session import async_session_maker
from src.database.models import UserSurvey, User
from src.bot.states import CoachState
from src.bot.keyboards.coach import get_coach_menu # Клавиатура чата
from src.bot.handlers.survey import start_coach_survey # Функция старта анкеты

router = Router()

@router.message(F.text == "🧠 AI Коуч (Питание/Спорт)")
async def coach_mode_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    async with async_session_maker() as session:
        # Проверяем, есть ли уже активная анкета коуча
        # Берем последнюю запись
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(UserSurvey.created_at.desc())
            .limit(1)
        )
        
        user = await session.get(User, user_id)
        
        # Если есть план и режим выбран
        if survey and survey.current_plan and user.coach_mode:
            # Сразу в чат
            await state.set_state(CoachState.chatting)
            await message.answer(
                "👋 С возвращением! Я помню наш план.\n"
                "О чем хочешь поговорить сегодня? Или посмотри меню 👇",
                reply_markup=get_coach_menu()
            )
        else:
            # Анкеты нет - запускаем
            await start_coach_survey(message, state)

@router.message(F.text == "ℹ️ Справка")
async def help_handler(message: Message):
    await message.answer(
        "🤖 <b>Как работает AI Коуч:</b>\n\n"
        "1. <b>Заполни анкету:</b> Расскажи о себе, питании и тренировках.\n"
        "2. <b>Получи план:</b> Искусственный интеллект составит программу.\n"
        "3. <b>Общайся:</b> Это не просто текст. Ты можешь писать боту: <i>'Замени рыбу на курицу'</i> или <i>'У меня болит колено, как тренироваться?'</i>.\n"
        "4. <b>Трекинг:</b> Бот сам напишет тебе в выбранное время, чтобы узнать успехи.\n\n"
        "<i>Для старта нажми кнопку 'AI Коуч' в меню.</i>"
    )

# Остальные кнопки (Астролог, Дейтинг) обрабатываются в своих роутерах, 
# они подключаются в main.py отдельно, тут их трогать не будем.