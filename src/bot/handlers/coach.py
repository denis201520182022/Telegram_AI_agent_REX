import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, desc, update
from src.utils.admin import is_admin
from src.bot.states import CoachState, SurveyState
from src.database.session import async_session_maker
from src.database.models import UserSurvey, User
from src.services.rabbit import send_to_queue
import asyncio 
from sqlalchemy.orm.attributes import flag_modified
from src.services.redis import redis_service
# Клавиатуры
from src.bot.keyboards.coach import (
    get_coach_menu, 
    get_coach_settings_kb, 
    get_change_format_choice_kb
)
from src.bot.keyboards.survey import get_coach_mode_kb


router = Router()

# --- ОСНОВНЫЕ КНОПКИ МЕНЮ КОУЧА ---

@router.message(CoachState.chatting, F.text == "📋 Мой план")
async def get_current_plan_btn(message: Message, state: FSMContext):
    # 1. Скрываем действие: удаляем сообщение пользователя "📋 Мой план"
    # try:
    #     #await message.delete()
    # except:
    #     pass

    # 2. Твоя секретная инструкция для ИИ
    system_instruction = (
        "Пользователь запрашивает свой текущий план. "
        "Твоя задача: напиши короткую вежливую фразу (например: 'Вот твой актуальный план:'), "
        "затем поставь тег [UPDATE_PLAN] и сразу после него выдай ПОЛНУЮ версию плана в HTML "
        "внутри тега <blockquote expandable>. "
        "Опирайся на последнюю версию плана и историю правок."
    )

    # 3. Отправляем задачу в AI Worker (режим chat, но с нашей инструкцией)
    # Мы добавляем эту инструкцию в историю сообщений как будто её написал юзер
    async with async_session_maker() as session:
        user = await session.get(User, message.from_user.id)
        if user:
            new_history = list(user.coach_history) if user.coach_history else []
            new_history.append({"role": "user", "content": system_instruction})
            user.coach_history = new_history
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(user, "coach_history")
            await session.commit()

    # Пушим задачу воркеру
    await send_to_queue("q_ai_generation", {
        "user_id": message.from_user.id,
        "mode": "chat"
    })


@router.message(CoachState.chatting, F.text == "⚙️ Настройки")
async def coach_settings(message: Message):
    """Меню настроек: трекинг и смена формата."""
    async with async_session_maker() as session:
        user = await session.get(User, message.from_user.id)
        is_tracking = user.is_tracking_enabled if user else False
        
    await message.answer(
        "⚙️ <b>Настройки вашего Коуча</b>\n\n"
        "Здесь вы можете управлять уведомлениями или изменить направление программы.",
        reply_markup=get_coach_settings_kb(is_tracking),
        parse_mode="HTML"
    )

@router.message(CoachState.chatting, F.text == "🏠 Выйти")
async def exit_coach_mode(message: Message, state: FSMContext):
    """Выход в главное меню с проверкой прав админа и кредитов."""
    user_id = message.from_user.id
    await state.clear()
    
    from src.bot.keyboards.menu import get_main_menu
    
    
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        admin_status = is_admin(user_id)
        menu_kb = get_main_menu(
            natal_credits=user.natal_chart_credits if user else 0,
            is_admin=admin_status
        )
        
    await message.answer("🏠 Вы вернулись в главное меню.", reply_markup=menu_kb)

# --- ОБРАБОТКА НАСТРОЕК (Callback) ---

@router.callback_query(F.data == "toggle_tracking")
async def toggle_tracking(callback: CallbackQuery):
    """Вкл/Выкл ежедневный трекинг."""
    async with async_session_maker() as session:
        user = await session.get(User, callback.from_user.id)
        if user:
            user.is_tracking_enabled = not user.is_tracking_enabled
            new_status = user.is_tracking_enabled
            await session.commit()
            
            await callback.message.edit_reply_markup(
                reply_markup=get_coach_settings_kb(new_status)
            )
            await callback.answer(f"Трекинг {'включен' if new_status else 'выключен'} ✅")

@router.callback_query(F.data == "change_coach_mode")
async def change_mode_start(callback: CallbackQuery):
    """Выбор: Адаптировать текущий план или Заполнить заново."""
    await callback.message.edit_text(
        "🔄 <b>Смена формата коучинга</b>\n\n"
        "Как поступим с текущим прогрессом?\n\n"
        "1. <b>Адаптировать</b> — ИИ переделает текущий план под новый режим (например, добавит тренировки к диете), сохраняя контекст.\n"
        "2. <b>Заново</b> — полная очистка и прохождение нового опроса.",
        reply_markup=get_change_format_choice_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

# src/bot/handlers/coach.py

@router.callback_query(F.data == "coach_reset")
async def coach_reset_handler(callback: CallbackQuery, state: FSMContext):
    """Полный сброс — очищаем историю в БД и отправляем на анкету."""
    user_id = callback.from_user.id
    
    async with async_session_maker() as session:
        # 1. Сбрасываем историю в базе данных в пустой список
        await session.execute(
            update(User).where(User.user_id == user_id).values(coach_history=[])
        )
        await session.commit()
    
    await callback.answer("История очищена 🗑", show_alert=True)
    
    # 2. Очищаем FSM и запускаем анкету
    await state.clear()
    from src.bot.handlers.survey import start_coach_survey
    await start_coach_survey(callback.message, state)

# src/bot/handlers/coach.py

@router.callback_query(F.data == "coach_adapt")
async def coach_adapt_handler(callback: CallbackQuery, state: FSMContext):
    """Адаптация — просим выбрать новый режим и подгружаем старые ответы."""
    user_id = callback.from_user.id
    
    async with async_session_maker() as session:
        # 1. Достаем последнюю анкету пользователя
        survey = await session.scalar(
            select(UserSurvey)
            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
            .order_by(desc(UserSurvey.created_at))
        )
        existing_answers = survey.answers if survey else {}

    # 2. Записываем в состояние: флаг адаптации И старые ответы
    await state.update_data(is_adaptation=True, answers=existing_answers)
    
    # 3. Переводим в состояние выбора режима
    await state.set_state(SurveyState.choosing_mode)

    await callback.message.edit_text(
        "Выберите новый формат для <b>адаптации</b> вашего текущего плана:",
        reply_markup=get_coach_mode_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

# Хендлер для выбора режима ПРИ АДАПТАЦИИ (перехватываем то, что обычно идет в survey)
@router.callback_query(CoachState.chatting, F.data.startswith("mode_"))
async def process_adaptation_mode_selection(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get('is_adaptation'):
        return # Если это не адаптация, пусть обрабатывает survey.py

    new_sub_mode = callback.data.split("_")[1] # nutrition / workout / combined
    user_id = callback.from_user.id

    await callback.message.edit_text("⏳ <b>Адаптирую ваш план под новый формат...</b>", parse_mode="HTML")

    async with async_session_maker() as session:
        # Обновляем режим пользователя в БД
        await session.execute(
            update(User).where(User.user_id == user_id).values(coach_mode=new_sub_mode)
        )
        # Ищем последнюю анкету, чтобы передать её ID воркеру
        survey = await session.scalar(
            select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach').order_by(desc(UserSurvey.created_at))
        )
        await session.commit()

        # Отправляем задачу воркеру с флагом адаптации
        task = {
            "user_id": user_id,
            "mode": "coach",
            "sub_mode": new_sub_mode,
            "answers": survey.answers if survey else {},
            "survey_id": survey.id if survey else None,
            "is_adaptation": True
        }
        await send_to_queue("q_ai_generation", task)
    
    await state.update_data(is_adaptation=False)
    await callback.answer()

# --- СВОБОДНОЕ ОБЩЕНИЕ ---
# src/bot/handlers/coach.py
@router.message(CoachState.chatting)
async def process_chat_message(message: Message):
    """
    Хендлер для текстовых сообщений ИИ-коучу.
    Реализовано: сохранение в историю БД и накопление сообщений (5 сек).
    """
    user_id = message.from_user.id
    text = message.text
    
    if not text:
        return 

    # 1. СОХРАНЕНИЕ В ИСТОРИЮ (PostgreSQL)
    # Мы записываем каждое сообщение сразу, чтобы история в базе была актуальной
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        if not user:
            return

        # Создаем копию списка, чтобы SQLAlchemy увидел изменения
        current_history = list(user.coach_history) if user.coach_history else []
        current_history.append({"role": "user", "content": text})
        
        # Лимит истории (20 сообщений), чтобы не раздувать контекст
        if len(current_history) > 20:
            current_history = current_history[-20:]
            
        user.coach_history = current_history
        
        # !!! ВАЖНО: говорим базе, что поле изменилось
        flag_modified(user, "coach_history")
        await session.commit()

    # 2. ЛОГИКА НАКОПЛЕНИЯ (Debounce через Redis)
    buffer_key = f"coach_buffer:{user_id}"
    wait_key = f"coach_wait:{user_id}"

    # Дописываем сообщение в буфер Redis (через пробел)
    # Твой RedisService позволяет обращаться к клиенту напрямую
    await redis_service.client.append(buffer_key, f" {text}")
    
    # Проверяем, не запущен ли уже таймер для этого пользователя
    if await redis_service.get(wait_key):
        # Если таймер уже идет, просто выходим. 
        # Текст уже в буфере, его заберет тот процесс, который запустил таймер.
        return

    # Если мы здесь — это первое сообщение в "пачке". Ставим замок на 10 сек.
    await redis_service.set(wait_key, "1", ex=10)
    
    # Показываем, что бот думает
    await message.bot.send_chat_action(chat_id=user_id, action="typing")
    
    # Ждем 5 секунд, пока пользователь допишет остальные мысли
    await asyncio.sleep(5)

    # 3. ФОРМИРУЕМ ЗАДАЧУ ДЛЯ ИИ
    # Забираем из Redis весь накопленный текст
    full_text = await redis_service.get(buffer_key)
    
    # Очищаем временные ключи в Redis
    await redis_service.delete(buffer_key)
    await redis_service.delete(wait_key)

    if not full_text:
        return

    # Отправляем одну агрегированную задачу в RabbitMQ
    task = {
        "user_id": user_id,
        "mode": "chat",
        "text": full_text.strip()
    }
    await send_to_queue("q_ai_generation", task)