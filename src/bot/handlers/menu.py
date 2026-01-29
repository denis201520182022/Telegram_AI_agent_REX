import json
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy import select, desc
import asyncio

# База и модели
from src.database.session import async_session_maker
from src.database.models import UserSurvey, User
from src.bot.states import CoachState, SurveyState
from src.bot.keyboards.dating import get_dating_menu

# Клавиатуры
from src.bot.keyboards.coach import get_coach_menu 
from src.bot.keyboards.menu import get_main_menu

# Сервисы
from src.services.horoscope import get_zodiac_sign, RUS_SIGNS
from src.services.redis import redis_service
from src.services.rabbit import send_to_queue

# Хендлеры
from src.bot.handlers.survey import start_coach_survey, start_generic_survey
from src.utils.admin import is_admin
from aiogram.types import ReplyKeyboardRemove
from src.bot.keyboards.horoscope import get_astro_menu

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
            # Проверяем статус паузы
            is_paused = survey.answers.get('is_paused', False)
            
            await message.answer(
                "❤️ <b>Режим знакомств</b>\n\n"
                "Ваша анкета активна. Выберите действие:",
                # Передаем статус в меню
                reply_markup=get_dating_menu(is_paused=is_paused),
                parse_mode="HTML"
            )
        else:
            # Анкеты нет — убираем меню и запускаем опрос
            await message.answer(
                "Чтобы найти партнера, нужно создать профиль.",
                reply_markup=ReplyKeyboardRemove()
            )
            await start_generic_survey(message, state, mode="dating")
# --- ЛОГИКА АСТРОЛОГА ---

@router.message(F.text == "🔮 Астро-прогноз")
async def horoscope_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with async_session_maker() as session:
        # Ищем анкету именно для ГОРОСКОПА
        survey = await session.scalar(
            select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'horoscope')
        )
        
        if survey and survey.answers:
            await message.answer(
                "🌌 <b>Режим Астролога</b>\n\n"
                "Я знаю вашу дату рождения. Хотите узнать прогноз?",
                reply_markup=get_astro_menu(),
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "👋 Для ежедневных прогнозов мне нужно знать только вашу дату рождения.",
                reply_markup=ReplyKeyboardRemove()
            )
            # Запускаем анкету режима horoscope
            await start_generic_survey(message, state, mode="horoscope")

@router.message(F.text == "✨ Получить прогноз на сегодня")
async def get_daily_forecast(message: Message):
    user_id = message.from_user.id
    async with async_session_maker() as session:
        survey = await session.scalar(
            select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'horoscope')
        )
        
        if not survey or not survey.answers:
            await message.answer("Сначала заполните данные.")
            return

        b_date_str = survey.answers.get('birth_date')
        try:
            from datetime import datetime
            b_date = datetime.strptime(b_date_str, "%Y-%m-%d").date()
            sign_en = get_zodiac_sign(b_date)
            sign_ru = RUS_SIGNS.get(sign_en, "Зодиак")
            
            # 1. Пробуем достать из кеша
            prediction = await redis_service.get(f"horoscope_daily:{sign_en}")
            
            if not prediction:
                # 2. Если кеш пуст (например, бот перезагрузился), запускаем генерацию
                await message.answer("🔭 <b>Телескопы настраиваются...</b>\nПрогнозы на сегодня обновляются. Попробуйте нажать кнопку через 10-15 секунд.")
                from src.workers.scheduler import update_daily_horoscopes
                # Запускаем в фоне, чтобы не блочить
                asyncio.create_task(update_daily_horoscopes())
                return

            # Выдаем случайный вариант
            variants = prediction.split('|')
            import random
            text = random.choice(variants).strip()
            await message.answer(f"🔮 <b>Гороскоп для знака {sign_ru}:</b>\n\n{text}", parse_mode="HTML")
            
        except Exception:
            await message.answer("Ошибка в дате рождения. Нажмите 'Изменить данные'.")

@router.message(F.text == "🔄 Изменить данные")
async def change_astro_data(message: Message, state: FSMContext):
    await message.answer("🔄 Обновляем дату рождения...", reply_markup=ReplyKeyboardRemove())
    await start_generic_survey(message, state, mode="horoscope")

@router.message(F.text == "🔙 Назад")
async def back_to_main(message: Message, state: FSMContext):
    # Универсальная кнопка "Назад", которая вернет в главное меню
    await state.clear()
    user_id = message.from_user.id
    
    # Чтобы корректно отрисовать меню (админ/кредиты), нужно проверить юзера
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        credits = user.natal_chart_credits if user else 0
        admin_status = is_admin(user_id)

    await message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_menu(natal_credits=credits, is_admin=admin_status)
    )

 # --- НАТАЛЬНАЯ КАРТА ---
@router.message(F.text.contains("🌟 Натальная карта"))
async def natal_chart_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    admin_status = is_admin(user_id)
    
    kb_back = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True)

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        
        if admin_status or (user and user.natal_chart_credits > 0):
            # Ищем анкету именно для НАТАЛЬНОЙ КАРТЫ
            survey = await session.scalar(
                select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'natal')
            )
            
            if not survey:
                await message.answer(
                    "📜 <b>Натальная карта</b>\n"
                    "Это сложный расчет. Мне нужны точная дата, время и место рождения.", 
                    reply_markup=ReplyKeyboardRemove(),
                    parse_mode="HTML"
                )
                return await start_generic_survey(message, state, mode="natal")
            
            if survey.ai_recommendation:
                # Уже есть готовая карта - просто показываем
                await message.answer(
                    f"✨ <b>Ваша Натальная карта:</b>\n\n{survey.ai_recommendation}",
                    reply_markup=kb_back,
                    parse_mode="HTML"
                )
            else:
                # Анкета есть, но генерация зависла или не прошла
                await message.answer("✨ Генерирую карту заново...", reply_markup=kb_back)
                task = {"user_id": user_id, "mode": "natal", "answers": survey.answers, "survey_id": survey.id}
                await send_to_queue("q_ai_generation", task)
        else:
            await message.answer("🔒 Доступ закрыт. Нужно 5 QR-кодов.")

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