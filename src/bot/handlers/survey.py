#src\bot\handlers\survey.py
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
import html
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
    # Извлекаем выбранный под-режим (workout / nutrition / combined)
    selected_sub_mode = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    # 1. ОПРЕДЕЛЯЕМ КАТЕГОРИИ
    # ВАЖНО: Категория 'coach' должна быть в таблице именно под этим именем!
    target_categories = ["coach"] 
    
    if selected_sub_mode == "workout":
        target_categories.append("workout")
    elif selected_sub_mode == "nutrition":
        target_categories.append("nutrition")
    elif selected_sub_mode == "combined":
        target_categories.extend(["workout", "nutrition"])

    # 2. ЗАГРУЖАЕМ ВОПРОСЫ ИЗ REDIS
    all_required_questions = []
    for cat in target_categories:
        questions = await redis_service.get_survey_config(cat)
        if questions:
            all_required_questions.extend(questions)
        else:
            logger.error(f"Survey Error: Category '{cat}' returned 0 questions from Redis!")

    if not all_required_questions:
        await callback.answer("Ошибка: вопросы не найдены!", show_alert=True)
        return

    # 3. ПОЛУЧАЕМ ТЕКУЩИЕ ОТВЕТЫ (для фильтрации)
    data = await state.get_data()
    is_adaptation = data.get('is_adaptation', False)
    
    # Если это "Заполнить заново", answers должны быть пустыми
    current_answers = data.get('answers', {})
    
    # Если адаптация — подтягиваем ответы из ПОСЛЕДНЕЙ анкеты коуча
    if is_adaptation and not current_answers:
        async with async_session_maker() as session:
            survey = await session.scalar(
                select(UserSurvey)
                .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
                .order_by(desc(UserSurvey.created_at))
            )
            if survey and survey.answers:
                current_answers = dict(survey.answers)

    # 4. ФИЛЬТРАЦИЯ (Оставляем только то, на что нет ответов)
    final_questions = []
    for q in all_required_questions:
        if q['key'] not in current_answers:
            final_questions.append(q)

    # Отладочный лог в консоль — проверь его!
    print(f"DEBUG: Mode: {selected_sub_mode}, Total: {len(all_required_questions)}, To Ask: {len(final_questions)}")

    # 5. ПРОВЕРКА НА ПУСТОТУ
    if not final_questions:
        await callback.message.edit_text("✅ Данные актуальны. Обновляю план...")
        return await finish_setup_for_adaptation(callback, state, selected_sub_mode, current_answers)

    # 6. ЗАПУСК
    await state.update_data(
        survey_mode=selected_sub_mode, 
        questions=final_questions,
        current_step=0, 
        answers=current_answers,
        msg_to_edit=callback.message.message_id
    )
    
    await state.set_state(SurveyState.in_progress)
    await _ask_question(callback.message, state)
    await callback.answer()


# --- ВАЖНО: Добавьте новую вспомогательную функцию в конец файла ---
# Она нужна для случая, когда дозадавать вопросы не нужно
async def finish_setup_for_adaptation(callback: CallbackQuery, state: FSMContext, new_sub_mode: str, answers: dict):
    user_id = callback.from_user.id
    
    async with async_session_maker() as session:
        # Обновляем режим тренера у юзера
        await session.execute(
            update(User).where(User.user_id == user_id).values(coach_mode=new_sub_mode)
        )

        # Обновляем существующую анкету или создаем новую
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at))
        )
        
        if survey:
            survey.answers = answers # Тут лежат и старые, и возможные новые ответы
            survey.sub_mode = new_sub_mode
            survey_id = survey.id
        else:
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

    task = {
        "user_id": user_id, 
        "mode": "coach", 
        "sub_mode": new_sub_mode,
        "answers": answers, 
        "survey_id": survey_id, 
        "is_adaptation": True
    }
    await send_to_queue("q_ai_generation", task)
    
    await state.set_state(CoachState.chatting)
    await state.update_data(is_adaptation=False, questions=None, current_step=0)
    await callback.message.answer("🚀 Режим изменен. Готовим обновленные рекомендации...")

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
    # Если в вопросе ПРЕДУСМОТРЕНЫ варианты выбора (неважно, text это или button)
    if q.get('options') and len(q['options']) > 0:
        kb = get_options_keyboard(q['options'])

    elif q['type'] == 'photo':
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Пропустить (без фото)", callback_data="skip_photo")],
            [InlineKeyboardButton(text="❌ Прервать", callback_data="cancel_survey")]
        ])
    else:
        # Если вариантов нет совсем, показываем просто кнопку отмены
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
        
    data = await state.get_data()
    questions = data.get('questions', [])
    current_step = data.get('current_step', 0)

    # === ДОБАВЛЯЕМ ПРЕДОХРАНИТЕЛЬ ===
    # Если шаг уже равен или больше кол-ва вопросов, значит опрос закончен, 
    # и мы просто застряли в этом состоянии из-за ошибки.
    if current_step >= len(questions):
        log.warning("survey_already_finished_but_state_stuck", user_id=message.from_user.id)
        return await _finish_questions(message, state)
    # ================================

    q = questions[current_step]
    
    if q['type'] == 'int':
        if not text.isdigit():
            temp_msg = await message.answer("🔢 Пожалуйста, введите число.")
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
    is_adaptation = data.get('is_adaptation', False)
    
    # Чтобы в сводке были красивые названия, берем тексты из текущего списка вопросов
    # Если это адаптация, в answers могут быть старые ключи, которых нет в текущем списке вопросов
    questions_map = {q['key']: q['text'] for q in data.get('questions', [])}
    
    summary = "📋 <b>Ваши ответы:</b>\n\n"
    hidden_keys = ["birth_date", "birth_day", "birth_month", "birth_year"]

    for key, val in answers.items():
        if key in hidden_keys:
            continue
            
        # Если ключа нет в текущем списке (это старый ответ), 
        # при адаптации его можно просто не показывать в сводке «новых» ответов
        if key not in questions_map and is_adaptation:
            continue
            
        q_text = questions_map.get(key, key)
        short_q = q_text.replace("✅ ", "").replace("🏃 ", "").replace("🥗 ", "").split('?')[0]
        
        safe_q = html.escape(short_q)
        safe_val = html.escape(str(val)) 

        summary += f"▫️ {safe_q}: <b>{safe_val}</b>\n"
        
    summary += "\nПроверьте, всё ли верно?"
    
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
        except Exception as e:
            # Если редактирование не удалось (например, сообщение слишком старое), шлем новое
            await message.answer(summary, reply_markup=get_confirm_kb())
    else:
        await message.answer(summary, reply_markup=get_confirm_kb())
        
    # Если адаптация и вопросов не было, то сразу идем в финальную обработку
    if is_adaptation and not data.get("questions"):
        return await confirm_survey(message, state)
        
    await state.set_state(SurveyState.confirmation)

@router.callback_query(SurveyState.confirmation, F.data == "survey_restart")
async def restart_survey(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mode = data.get('survey_mode') # Достаем текущий режим из памяти
    
    await callback.answer("🔄 Заполняем заново...")
    
    # Если это один из под-режимов коуча, возвращаем на выбор "Питание/Спорт"
    if mode in ['nutrition', 'workout', 'combined']:
        await start_coach_survey(callback.message, state)
    else:
        # Иначе запускаем ту же самую анкету (dating, natal, horoscope)
        await start_generic_survey(callback.message, state, mode=mode)


@router.callback_query(SurveyState.confirmation, F.data == "survey_confirm")
async def confirm_survey(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mode = data.get('survey_mode')
    user_id = callback.from_user.id
    is_adaptation = data.get('is_adaptation', False)

    if mode in ['dating', 'natal', 'horoscope']:
        await _finish_non_coach_survey(callback.message, state)
        return await callback.answer("✅ Данные сохранены!")

    # --- ЛОГИКА ДЛЯ КОУЧА ---
    if is_adaptation:
        # Для адаптации сразу идем в финал, не спрашивая часовой пояс и политику
        await callback.message.edit_text("⏳ <b>Обновляю настройки и генерирую новый план...</b>", parse_mode="HTML")
        # Вызываем функцию финализации напрямую
        return await finish_setup(callback, state)

    # Если новая анкета — проверяем политику и часовой пояс
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

from sqlalchemy.orm.attributes import flag_modified

@router.callback_query(SurveyState.choosing_time, F.data.startswith("time_"))
@router.callback_query(SurveyState.confirmation) # Добавляем возможность вызова из подтверждения
async def finish_setup(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    is_adaptation = data.get('is_adaptation', False)
    user_id = callback.from_user.id
    answers = data.get('answers', {})
    mode = data.get('survey_mode')

    # Инициализируем переменные для новой анкеты
    track_time = None
    tz = data.get('selected_timezone')
    
    # Если это НЕ адаптация, вытаскиваем время из нажатой кнопки
    if not is_adaptation and callback.data and callback.data.startswith("time_"):
        try:
            time_str = callback.data.split("_")[1]
            h, m = map(int, time_str.split(":"))
            track_time = datetime.time(h, m)
        except Exception as e:
            logger.error(f"Error parsing time: {e}")

    # Если мы пришли сюда через адаптацию (edit_text уже был сделан в confirm_survey), 
    # проверяем, нужно ли менять текст
    if not is_adaptation:
        await callback.message.edit_text("⏳ <b>Анализирую данные и составляю план...</b>", reply_markup=None, parse_mode="HTML")
    
    survey_id = None
    async with async_session_maker() as session:
        # 1. Обновляем юзера
        update_vals = {"coach_mode": mode}
        if not is_adaptation:
            update_vals.update({
                "timezone": tz, 
                "tracking_time": track_time, 
                "is_tracking_enabled": True
            })
        
        await session.execute(update(User).where(User.user_id == user_id).values(**update_vals))
        
        # 2. Работа с анкетой
        # Ищем последнюю анкету коуча
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at))
        )
        
        if is_adaptation and survey:
            # При адаптации ОБНОВЛЯЕМ последнюю анкету
            survey.answers = answers
            survey.sub_mode = mode
            flag_modified(survey, "answers") # Явно помечаем для алхимии, что JSON изменился
            survey_id = survey.id
        else:
            # Создаем новую анкету
            new_survey = UserSurvey(
                user_id=user_id, 
                mode="coach", 
                sub_mode=mode, 
                answers=answers, 
                survey_config_id=1
            )
            session.add(new_survey)
            await session.flush()
            survey_id = new_survey.id
        
        await session.commit()

    # 3. Отправка задачи в AI Worker
    task = {
        "user_id": user_id, 
        "mode": "coach", 
        "sub_mode": mode, 
        "answers": answers, 
        "survey_id": survey_id, 
        "is_adaptation": is_adaptation
    }
    await send_to_queue("q_ai_generation", task)
    
    # 4. Завершение
    await state.set_state(CoachState.chatting)
    await state.update_data(is_adaptation=False)
    
    # Финальное уведомление (чтобы юзер не висел на экране загрузки)
    await callback.message.answer(
        "🚀 <b>Данные приняты!</b>\n\n"
        "Я начал составлять ваш обновленный план. Это займет немного времени"
        parse_mode="HTML"
    )
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