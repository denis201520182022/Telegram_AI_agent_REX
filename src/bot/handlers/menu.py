import json
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, desc

# База и модели
from src.database.session import async_session_maker
from src.database.models import UserSurvey, User
from src.bot.states import CoachState, SurveyState

# Клавиатуры
from src.bot.keyboards.coach import get_coach_menu 
from src.bot.keyboards.menu import get_main_menu

# Сервисы
from src.services.horoscope import get_zodiac_sign, RUS_SIGNS
from src.services.redis import redis_service
from src.services.rabbit import send_to_queue

# Хендлеры
from src.bot.handlers.survey import start_coach_survey, start_generic_survey
from src.bot.handlers.main import is_admin

router = Router()

@router.message(F.text == "🧠 AI Коуч (Питание/Спорт)")
async def coach_mode_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with async_session_maker() as session:
        # Ищем последний активный план
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at)).limit(1)
        )
        user = await session.get(User, user_id)
        
        if survey and survey.current_plan and user.coach_mode:
            await state.set_state(CoachState.chatting)
            await message.answer(
                "👋 С возвращением в режим Коуча! Я готов продолжать нашу работу.\n\n"
                "Вы можете задать любой вопрос или воспользоваться меню ниже 👇",
                reply_markup=get_coach_menu()
            )
        else:
            # Если плана нет или режим не выбран — на анкету
            await start_coach_survey(message, state)

@router.message(F.text == "❤️ Найти партнера")
async def dating_mode_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with async_session_maker() as session:
        survey = await session.scalar(
            select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'dating')
        )
        if survey:
            await message.answer(
                "✅ <b>Ваша анкета знакомств активна!</b>\n\n"
                "Раз в сутки я буду присылать вам подходящего партнера на основе ваших интересов.\n"
                "<i>Изменить анкету можно будет позже в настройках профиля.</i>",
                parse_mode="HTML"
            )
        else:
            await message.answer("Для участия в знакомствах нужно заполнить небольшую анкету.")
            await start_generic_survey(message, state, mode="dating")

@router.message(F.text == "🔮 Астро-прогноз")
async def horoscope_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with async_session_maker() as session:
        # Проверяем, есть ли данные рождения (анкета 'natal')
        survey = await session.scalar(
            select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'natal')
        )
        
        if survey and survey.answers:
            # Извлекаем дату из анкеты
            b_date_str = survey.answers.get('birth_date') # Ожидаем YYYY-MM-DD
            try:
                from datetime import datetime
                b_date = datetime.strptime(b_date_str, "%Y-%m-%d").date()
                sign_en = get_zodiac_sign(b_date)
                sign_ru = RUS_SIGNS.get(sign_en, "Зодиак")
                
                # Берем прогноз из Redis (обновляется планировщиком в 8 утра)
                prediction = await redis_service.get(f"horoscope_daily:{sign_en}")
                
                if prediction:
                    # У нас там 3 варианта через |, берем один случайный
                    variants = prediction.split('|')
                    import random
                    text = random.choice(variants).strip()
                    await message.answer(f"🔮 <b>Прогноз для знака {sign_ru}:</b>\n\n{text}", parse_mode="HTML")
                else:
                    await message.answer(f"🔮 <b>{sign_ru}:</b> Ваш прогноз на сегодня подготавливается. Попробуйте чуть позже.")
            except Exception:
                await message.answer("Ошибка в данных рождения. Перезаполните анкету астролога.")
                await start_generic_survey(message, state, mode="natal")
        else:
            await message.answer("Чтобы я мог составить прогноз, мне нужно знать вашу дату рождения.")
            await start_generic_survey(message, state, mode="natal")

@router.message(F.text.contains("🌟 Натальная карта"))
async def natal_chart_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    admin_status = is_admin(user_id)
    
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        
        # Проверка прав доступа
        if admin_status or (user and user.natal_chart_credits > 0):
            # 1. Проверяем, заполнена ли анкета рождения
            survey = await session.scalar(
                select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'natal')
            )
            
            if not survey:
                await message.answer("Для составления натальной карты сначала заполните данные рождения.")
                return await start_generic_survey(message, state, mode="natal")
            
            # 2. Если анкета есть, проверяем, есть ли уже готовая рекомендация
            if survey.ai_recommendation:
                await message.answer(
                    f"✨ <b>Ваша Натальная карта:</b>\n\n{survey.ai_recommendation}",
                    parse_mode="HTML"
                )
            else:
                # 3. Если анкету заполнил, но расчет еще не делали (или в очереди)
                await message.answer("✨ <b>Начинаю глубокий анализ...</b>\nЗвезды выстраиваются в ряд. Это займет около минуты.")
                
                # Отправляем задачу в очередь AI Worker (один раз)
                task = {
                    "user_id": user_id,
                    "mode": "natal",
                    "answers": survey.answers,
                    "survey_id": survey.id
                }
                await send_to_queue("q_ai_generation", task)
                
                # Списываем кредит (если не админ)
                if not admin_status:
                    user.natal_chart_credits -= 1
                    await session.commit()
        else:
            await message.answer("🔒 <b>Натальная карта заблокирована</b>\n\nАктивируйте 5 QR-кодов, чтобы получить 1 расчет.")

@router.message(F.text == "ℹ️ Справка")
async def help_handler(message: Message):
    help_text = (
        "🤖 <b>Ваш персональный помощник REX</b>\n\n"
        "🥗 <b>AI Коуч:</b> Составит план питания и тренировок, ответит на любые вопросы в чате.\n"
        "❤️ <b>Знакомства:</b> Ищет партнеров со схожими интересами в спорте.\n"
        "🔮 <b>Астрология:</b> Ежедневные прогнозы и детальная натальная карта.\n\n"
        "📍 <b>Как получить доступ?</b>\n"
        "Сканируйте QR-коды на продукции. Каждый код дает 5 дней доступа. 5 кодов открывают Натальную карту."
    )
    await message.answer(help_text, parse_mode="HTML")