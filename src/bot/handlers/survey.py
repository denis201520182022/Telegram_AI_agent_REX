import json
import datetime
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import update
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy import select, desc

from src.utils.admin import is_admin
from src.bot.keyboards.menu import get_main_menu
from src.database.session import async_session_maker
from src.database.models import User
from src.utils.logger import logger

# Импорты проекта
from src.database.session import async_session_maker
from src.database.models import User, UserSurvey
from src.services.redis import redis_service
from src.services.rabbit import send_to_queue
from src.bot.states import SurveyState, CoachState
from src.bot.keyboards.survey import (
    get_coach_mode_kb, get_confirm_kb, get_policy_kb, 
    get_timezone_kb, get_tracking_time_kb, get_cancel_kb
)

router = Router()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def safe_delete(bot, chat_id, message_id):
    try: await bot.delete_message(chat_id, message_id)
    except Exception: pass

def get_options_keyboard(options: list) -> types.InlineKeyboardMarkup:
    """Генерирует компактные инлайн-кнопки для ответов"""
    keyboard = []
    row = []
    
    # Определяем ширину ряда: если варианты короткие (числа), делаем 6 в ряд
    # Если длинные — 2 в ряд
    max_in_row = 6 if all(len(str(opt)) <= 3 for opt in options) else 2

    for opt in options:
        cb_data = f"ans_{opt}"[:64]
        row.append(types.InlineKeyboardButton(text=str(opt), callback_data=cb_data))
        if len(row) == max_in_row:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_survey")])
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)
# --- 1. СТАРТ АНКЕТЫ ---

# Универсальный старт анкеты
async def start_generic_survey(message: Message, state: FSMContext, mode: str):
    await state.clear()
    
    # Загружаем вопросы именно для этого режима из Redis
    # Убедись, что в Google Sheets на листе 1 есть категории 'dating' и 'natal'
    all_questions = await redis_service.get_survey_config(mode)
    
    if not all_questions:
        await message.answer(f"Ошибка: вопросы для режима {mode} не найдены.")
        return

    await state.update_data(
        survey_mode=mode,
        questions=all_questions,
        current_step=0,
        answers={},
        msg_to_edit=None # Для простоты новых анкет шлем новые сообщения
    )
    
    await state.set_state(SurveyState.in_progress)
    await _ask_question(message, state)

# Это старый метод для коуча, его оставляем, он вызывает выбор под-режима
@router.message(F.text == "📝 Начать работу с Коучем") 
async def start_coach_survey(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 С чем будем работать?",
        reply_markup=get_coach_mode_kb()
    )
    await state.set_state(SurveyState.choosing_mode)

# src/bot/handlers/survey.py

# ... (импорты)
# src/bot/handlers/survey.py

@router.callback_query(SurveyState.choosing_mode, F.data.startswith("mode_"))
async def process_mode_selection(callback: CallbackQuery, state: FSMContext):
    selected_mode = callback.data.split("_")[1]
    
    # 1. Загружаем все вопросы для коуча
    all_coach_questions = await redis_service.get_survey_config("coach")
    if not all_coach_questions:
        await callback.answer("Ошибка: вопросы не загружены.", show_alert=True)
        return
        
    data = await state.get_data()
    is_adaptation = data.get('is_adaptation', False)
    current_answers = data.get('answers', {}) # Те самые ответы, что мы подгрузили выше

    # 2. Определяем нужные категории для НОВОГО режима
    categories_needed = {"coach"} # Базовые вопросы
    if selected_mode in ["nutrition", "combined"]:
        categories_needed.add("nutrition")
    if selected_mode in ["workout", "combined"]:
        categories_needed.add("workout")
    
    # 3. Фильтруем вопросы по категориям
    target_questions = [q for q in all_coach_questions if q.get("category") in categories_needed]
    
    # --- ЛОГИКА УМНОЙ ФИЛЬТРАЦИИ ---
    final_questions_to_ask = []
    if is_adaptation:
        # Оставляем только те вопросы, ключей которых НЕТ в текущих ответах
        for q in target_questions:
            if q['key'] not in current_answers:
                final_questions_to_ask.append(q)
    else:
        # Если не адаптация — задаем всё
        final_questions_to_ask = target_questions
        current_answers = {} # Сбрасываем ответы для чистого прохождения

    # 4. Если после фильтрации вопросов не осталось — сразу на генерацию
    if not final_questions_to_ask:
        await callback.message.edit_text("⏳ <b>Все данные уже есть. Начинаю адаптацию плана...</b>", parse_mode="HTML")
        return await finish_setup_for_adaptation(callback, state, selected_mode)

    # 5. Если вопросы есть — запускаем анкету
    await state.update_data(
        survey_mode=selected_mode,
        questions=final_questions_to_ask,
        current_step=0,
        answers=current_answers, # Здесь уже лежат старые ответы
        msg_to_edit=callback.message.message_id
    )
    
    await state.set_state(SurveyState.in_progress)
    await _ask_question(callback.message, state)
    await callback.answer()

# --- ВАЖНО: Добавьте новую вспомогательную функцию в конец файла ---
# Она нужна для случая, когда дозадавать вопросы не нужно
async def finish_setup_for_adaptation(callback: CallbackQuery, state: FSMContext, new_sub_mode: str):
    """
    Завершает процесс адаптации, если новых вопросов задавать не потребовалось.
    Обновляет режим в БД и отправляет задачу ИИ.
    """
    user_id = callback.from_user.id
    data = await state.get_data()
    answers = data.get('answers', {})
    
    async with async_session_maker() as session:
        # 1. ОБЯЗАТЕЛЬНО обновляем coach_mode в таблице User
        # Чтобы ИИ воркер при генерации промпта понимал новый контекст (диетолог/тренер)
        await session.execute(
            update(User).where(User.user_id == user_id).values(coach_mode=new_sub_mode)
        )

        # 2. Обновляем саму анкету
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at))
        )
        
        if survey:
            # Обновляем ответы (если в процессе адаптации что-то добавилось) и под-режим
            survey.answers = answers
            survey.sub_mode = new_sub_mode
            survey_id = survey.id
        else:
            # Если вдруг анкеты не было (защита от ошибок)
            new_survey = UserSurvey(
                user_id=user_id, 
                mode='coach', 
                sub_mode=new_sub_mode, 
                answers=answers, 
                survey_config_id=1
            )
            session.add(new_survey)
            await session.flush()
            survey_id = new_survey.id
        
        await session.commit()

    # 3. Отправляем задачу воркеру с флагом адаптации (история в БД НЕ ТРОНУТА)
    task = {
        "user_id": user_id, 
        "mode": "coach", 
        "sub_mode": new_sub_mode,
        "answers": answers, 
        "survey_id": survey_id, 
        "is_adaptation": True
    }
    await send_to_queue("q_ai_generation", task)
    
    # 4. Переводим в режим чата и очищаем временные флаги анкеты
    await state.set_state(CoachState.chatting)
    await state.update_data(is_adaptation=False, questions=None, current_step=0)

# --- 2. ЗАДАЕМ ВОПРОСЫ ---
async def _ask_question(message: Message, state: FSMContext):
    data = await state.get_data()
    questions = data['questions']
    step = data['current_step']
    mode = data.get('survey_mode', 'coach') # Получаем режим
    msg_id = data.get('msg_to_edit')
    
    if step >= len(questions):
        return await _finish_questions(message, state)
        
    q = questions[step]
    
    # --- РАСЧЕТ ПРОГРЕСС-БАРА ---
    total = len(questions)
    filled = step
    percent = int((filled / total) * 100)
    bar_size = 10
    filled_size = int((filled / total) * bar_size)
    progress_bar = "🟦" * filled_size + "⬜️" * (bar_size - filled_size)
    
    # --- ДИНАМИЧЕСКИЙ ЗАГОЛОВОК ---
    titles = {
        "coach": "🏃 <b>Анкета Коуча</b>",
        "dating": "🤝 <b>Анкета Знакомств</b>",
        "natal": "📜 <b>Натальная Карта</b>",
        "horoscope": "📅 <b>Дата рождения</b>" # <--- Добавили
    }
    title = titles.get(mode, "📝 <b>Анкета</b>")
    
    text = (
        f"{title}\n"
        f"Этап: {step + 1} из {total}\n"
        f"{progress_bar} {percent}%\n\n"
        f" <b>{q['text']}</b>"
    )
    
    kb = None
    if q['type'] == 'button' and q.get('options'):
        kb = get_options_keyboard(q['options'])

    elif q['type'] == 'photo':
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Пропустить (без фото)", callback_data="skip_photo")],
            [InlineKeyboardButton(text="❌ Прервать", callback_data="cancel_survey")]
        ])
    else:
        kb = get_cancel_kb()

    if msg_id:
        try:
            await message.bot.edit_message_text(
                text=text,
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=kb,
                parse_mode="HTML"
            )
            return
        except: pass
            
    sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.update_data(msg_to_edit=sent.message_id)

# --- 3. ОБРАБОТКА ОТВЕТОВ ---

@router.callback_query(SurveyState.in_progress, F.data.startswith("ans_"))
async def process_button_answer(callback: CallbackQuery, state: FSMContext):
    answer = callback.data[4:] # ans_Мужской -> Мужской
    await _save_answer_and_next(callback.message, state, answer)
    await callback.answer()
# Обработка фото (если тип вопроса 'photo')
@router.message(SurveyState.in_progress, F.photo)
async def process_photo_answer(message: Message, state: FSMContext):
    # Берем ID самого большого фото
    photo_id = message.photo[-1].file_id
    await _save_answer_and_next(message, state, photo_id)

# Обработка кнопки "Пропустить" для фото
@router.callback_query(SurveyState.in_progress, F.data == "skip_photo")
async def process_skip_photo(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Сохраняем None, значит фото нет
    await _save_answer_and_next(callback.message, state, None)

@router.message(SurveyState.in_progress)
async def process_text_answer(message: Message, state: FSMContext):
    # Удаляем сообщение пользователя для чистоты чата
    await safe_delete(message.bot, message.chat.id, message.message_id)
    
    text = message.text
    if not text:
        return # Игнорим картинки и прочее
        
    # Валидация int
    data = await state.get_data()
    q = data['questions'][data['current_step']]
    
    if q['type'] == 'int':
        if not text.isdigit():
            # Показываем ошибку во всплывашке, но не двигаемся дальше
            temp_msg = await message.answer("🔢 Пожалуйста, введите число.")
            # Удалим через 3 сек
            import asyncio
            await asyncio.sleep(2)
            await safe_delete(message.bot, message.chat.id, temp_msg.message_id)
            return
            
    await _save_answer_and_next(message, state, text)

async def _save_answer_and_next(message: Message, state: FSMContext, answer):
    data = await state.get_data()
    # ДОБАВЛЯЕМ ПРОВЕРКУ ЗДЕСЬ
    if 'answers' not in data:
        # Если ключа answers нет, то логируем и инициализируем его
        logger.error("answers_not_found_in_state", user_id=message.from_user.id, state_data=data)
        await state.update_data(answers={}) # Инициализируем answers
        data = await state.get_data() # Обновляем data, т.к. мы его изменили
    step = data['current_step']
    questions = data['questions']
    answers = data['answers']
    
    current_key = questions[step]['key']
    
    # ЛОГИКА ТРАНСФОРМАЦИИ МЕСЯЦЕВ
    months_map = {
        "Январь": "01", "Февраль": "02", "Март": "03", "Апрель": "04",
        "Май": "05", "Июнь": "06", "Июль": "07", "Август": "08",
        "Сентябрь": "09", "Октябрь": "10", "Ноябрь": "11", "Декабрь": "12"
    }
    if current_key == "birth_month":
        answer = months_map.get(answer, "01")
    
    # Сохраняем текущий ответ
    answers[current_key] = answer
    
    # ПРОВЕРКА: Если мы получили и день, и месяц (и год для наталки), склеиваем их в birth_date
    if "birth_day" in answers and "birth_month" in answers:
        day = str(answers["birth_day"]).zfill(2)
        month = str(answers["birth_month"])
        # Год берем из ответов, если его нет (для гороскопа) — ставим любой дефолтный
        year = answers.get("birth_year", "2000") 
        answers["birth_date"] = f"{year}-{month}-{day}"

    await state.update_data(answers=answers, current_step=step + 1)
    await _ask_question(message, state)

# --- 4. ФИНАЛ АНКЕТЫ И ПОДТВЕРЖДЕНИЕ ---

# src/bot/handlers/survey.py

async def _finish_questions(message: Message, state: FSMContext):
    data = await state.get_data()
    answers = data['answers']
    mode = data.get('survey_mode')
    is_adaptation = data.get('is_adaptation', False)
    
    # Формируем красивый список ответов
    summary = "📋 <b>Ваши ответы:</b>\n\n"
    questions_map = {q['key']: q['text'] for q in data['questions']}
    
    for key, val in answers.items():
        q_text = questions_map.get(key, key)
        # Обрезаем вопрос, если длинный
        short_q = (q_text[:30] + '..') if len(q_text) > 30 else q_text
        summary += f"▫️ <i>{short_q}</i>: <b>{val}</b>\n"
        
    summary += "\nПроверьте"
    
    msg_id = data.get('msg_to_edit')
    if msg_id:
        try:
            await message.bot.edit_message_text(
                text=summary,
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=get_confirm_kb(),
                parse_mode="HTML"
            )
        except:
            await message.answer(summary, reply_markup=get_confirm_kb())
    else:
        await message.answer(summary, reply_markup=get_confirm_kb())
        
    # Если адаптация и вопросов не было, то сразу идем в финальную обработку
    if is_adaptation and not data.get("questions"):
        return await confirm_survey(message, state)
        
    await state.set_state(SurveyState.confirmation)

@router.callback_query(SurveyState.confirmation, F.data == "survey_restart")
async def restart_survey(callback: CallbackQuery, state: FSMContext):
    await callback.answer("🔄 Заполняем заново...")
    await start_coach_survey(callback.message, state)
@router.callback_query(SurveyState.confirmation, F.data == "survey_confirm")
async def confirm_survey(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mode = data.get('survey_mode')
    user_id = callback.from_user.id

    # Если это НЕ коуч (Дейтинг или Натальная карта), завершаем сразу
    if mode in ['dating', 'natal', 'horoscope']:
        await _finish_non_coach_survey(callback.message, state)
        await callback.answer("✅ Данные сохранены!")
        return

    # Для коуча — стандартная проверка политики и переход к таймзонам
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        has_accepted = user.has_accepted_policy if user else False
        
    if has_accepted:
        await _ask_timezone(callback.message, state)
    else:
        await callback.message.edit_text(
            "📜 <b>Разрешение на обработку данных</b>\n\n"
            "Для работы мне нужно обрабатывать ваши ответы. Я использую их только "
            "для генерации плана и не передаю третьим лицам.\n\nСогласны?",
            reply_markup=get_policy_kb(),
            parse_mode="HTML"
        )
        await state.set_state(SurveyState.policy_acceptance)
    await callback.answer()

# --- 5. ПОЛИТИКА, ТАЙМЗОНА, ВРЕМЯ ---

@router.callback_query(SurveyState.policy_acceptance, F.data == "policy_accept")
async def accept_policy(callback: CallbackQuery, state: FSMContext):
    # Сохраняем в БД сразу
    async with async_session_maker() as session:
        stmt = update(User).where(User.user_id == callback.from_user.id).values(has_accepted_policy=True)
        await session.execute(stmt)
        await session.commit()
    
    await _ask_timezone(callback.message, state)
    await callback.answer()

@router.callback_query(SurveyState.policy_acceptance, F.data == "policy_decline")
async def decline_policy(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Вы отказались. Без этого я не могу работать.")
    await state.clear()

async def _ask_timezone(message: Message, state: FSMContext):
    await message.edit_text(
        "🌍 <b>Выберите ваш город</b> (или ближайший по времени):",
        reply_markup=get_timezone_kb(),
        parse_mode="HTML"
    )
    await state.set_state(SurveyState.choosing_timezone)

@router.callback_query(SurveyState.choosing_timezone, F.data.startswith("tz_"))
async def process_timezone(callback: CallbackQuery, state: FSMContext):
    timezone = callback.data[3:] # tz_Europe/Moscow -> Europe/Moscow
    await state.update_data(selected_timezone=timezone)
    
    await callback.message.edit_text(
        "⏰ <b>Выберите удобное время</b>\n"
        "Когда мне писать, чтобы спросить про успехи?",
        reply_markup=get_tracking_time_kb(),
        parse_mode="HTML"
    )
    await state.set_state(SurveyState.choosing_time)
    await callback.answer()

# --- 6. ФИНАЛИЗАЦИЯ ---

@router.callback_query(SurveyState.choosing_time, F.data.startswith("time_"))
async def finish_setup(callback: CallbackQuery, state: FSMContext):
    time_str = callback.data.split("_")[1] # 09:00
    # Преобразуем в объект time
    h, m = map(int, time_str.split(":"))
    track_time = datetime.time(h, m)
    
    data = await state.get_data()
    answers = data['answers']
    mode = data['survey_mode'] # nutrition, workout, combined
    tz = data.get('selected_timezone', 'UTC')
    user_id = callback.from_user.id
    
    await callback.message.edit_text("⏳ <b>Спасибо! Анализирую данные и составляю план...</b>\n<i>Это займет около 30-60 секунд.</i>", reply_markup=None, parse_mode="HTML")
    
    # 1. Сохраняем всё в БД
    async with async_session_maker() as session:
        # Обновляем юзера
        u_stmt = update(User).where(User.user_id == user_id).values(
            timezone=tz,
            tracking_time=track_time,
            coach_mode=mode,
            is_tracking_enabled=True
        )
        await session.execute(u_stmt)
        
        # Создаем запись анкеты
        # sub_mode=mode, а основной mode='coach'
        survey = UserSurvey(
            user_id=user_id,
            mode="coach",
            sub_mode=mode,
            survey_config_id=1, # Заглушка, можно брать реальный ID конфига
            answers=answers
        )
        session.add(survey)
        await session.flush()
        survey_id = survey.id
        await session.commit()
    
    is_adaptation = data.get('is_adaptation', False)

    # 2. Отправляем задачу AI Worker-у
    task = {
        "user_id": user_id,
        "mode": "coach",
        "sub_mode": mode,
        "answers": answers,
        "survey_id": survey_id,
        "is_adaptation": is_adaptation # Передаем флаг адаптации
    }
    await send_to_queue("q_ai_generation", task)
    
    # 3. Переводим юзера в режим чата
    await state.set_state(CoachState.chatting)
    await callback.answer()

# --- ОТМЕНА ---
@router.callback_query(F.data == "cancel_survey")
async def cancel_survey(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await state.clear()
    
    # Восстанавливаем Главное Меню
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        credits = user.natal_chart_credits if user else 0
        admin_status = is_admin(user_id)

    # Удаляем сообщение с вопросом, чтобы не засорять чат, или редактируем его
    try:
        await callback.message.delete()
    except:
        pass
        
    await callback.message.answer(
        "❌ Анкета прервана.\n🏠 Возвращаемся в главное меню.", 
        reply_markup=get_main_menu(natal_credits=credits, is_admin=admin_status)
    )
    await callback.answer()


async def _finish_non_coach_survey(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data['survey_mode']
    answers = data['answers']
    user_id = message.chat.id

    async with async_session_maker() as session:
        # Сохраняем ответы в БД
        survey = UserSurvey(
            user_id=user_id,
            mode=mode,
            survey_config_id=1,
            answers=answers
        )
        session.add(survey)
        await session.flush()
        survey_id = survey.id
        await session.commit()

    # --- РАЗВИЛКА ПО РЕЖИМАМ ---
    
    if mode == 'horoscope':
        # Просто сохранили и вернули меню. НИКАКОЙ ГЕНЕРАЦИИ ЧЕРЕЗ AI WORKER.
        from src.bot.keyboards.horoscope import get_astro_menu
        await message.answer(
            "✅ <b>Дата рождения сохранена!</b>\nТеперь вы можете получать ежедневные прогнозы.", 
            parse_mode="HTML",
            reply_markup=get_astro_menu()
        )

    elif mode == 'natal':
        # А вот тут запускаем тяжелую генерацию
        await message.answer(
            "✨ <b>Данные приняты!</b>\nЗвезды выстраиваются в ряд... Я пришлю Натальную карту, как только она будет готова (1-2 минуты).", 
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True)
        )
        
        # Списываем кредит (если не админ) - логику можно добавить тут или оставить в menu entry
        # Для надежности шлем задачу
        task = {"user_id": user_id, "mode": "natal", "answers": answers, "survey_id": survey_id}
        await send_to_queue("q_ai_generation", task)

    elif mode == 'dating':
        from src.bot.keyboards.dating import get_dating_menu
        await message.answer(
            "✅ <b>Анкета знакомств обновлена!</b>", 
            parse_mode="HTML",
            reply_markup=get_dating_menu()
        )

    await state.clear()