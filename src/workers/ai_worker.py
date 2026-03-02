#src\workers\ai_worker.py
import asyncio
import json
import sys
import time
import aio_pika
from sqlalchemy import select, update, desc
from os.path import abspath, dirname
from sqlalchemy.orm.attributes import flag_modified
# Магия путей
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.config import settings
from src.services.llm import generate_response
from src.database.session import async_session_maker
from src.database.models import UserSurvey, User
from src.services.redis import redis_service 
from src.bot.keyboards.coach import get_coach_menu
from src.services.rabbit import send_to_queue

# OBSERVABILITY
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server, AI_TASK_PROCESSED, AI_TASK_DURATION
from src.utils.alerting import send_alert
from src.utils.text import clean_html_for_telegram
# src/workers/ai_worker.py


from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

bot_instance = None

async def keep_typing(chat_id: int, stop_event: asyncio.Event):
    """Фоновая задача для поддержания статуса 'печатает'."""
    global bot_instance
    while not stop_event.is_set():
        try:
            await bot_instance.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4.5) # Обновляем каждые 4.5 секунды
        except Exception:
            break

async def process_task(message: aio_pika.IncomingMessage):
    """
    Основной обработчик задач ИИ.
    Режимы: coach (план/адаптация), chat (свободное общение), natal (натальная карта), tracking_trigger.
    """
    async with message.process():
        start_time = time.time()

        try:
            task = json.loads(message.body)
        except json.JSONDecodeError:
            logger.error("json_decode_error", message_body=message.body)
            return

        user_id = task.get('user_id')
        mode = task.get('mode')
        log = logger.bind(user_id=user_id, mode=mode, worker="ai_worker")
        log.info("task_started")
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(keep_typing(user_id, stop_typing))
        try:
            async with async_session_maker() as session:

                # --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ПРОМПТА ---
                async def _get_prompt(prompt_key: str) -> str:
                    prompt_template = await redis_service.get_prompt(prompt_key)
                    if not prompt_template:
                        log.warning("prompt_not_found_in_redis", prompt_key=prompt_key)
                        return "Ты эмпатичный коуч."
                    return prompt_template

                # --- СЦЕНАРИЙ 1: КОУЧ (Первая генерация или Адаптация плана) ---
                if mode == 'coach':
                    sub_mode = task.get('sub_mode', 'combined') # Добавь дефолт 'combined' на всякий случай
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')
                    is_adaptation = task.get('is_adaptation', False)

                    user = await session.get(User, user_id)
                    # Находим последнюю анкету, чтобы взять из неё текущий план
                    survey = await session.get(UserSurvey, survey_id) 

                    old_plan_context = ""
                    # Если это адаптация и у нас есть старый план в базе
                    if is_adaptation and survey and survey.current_plan:
                        old_plan_context = f"\n\nТЕКУЩИЙ ПЛАН КЛИЕНТА (нужно адаптировать):\n{survey.current_plan}"

                    prompt_key = f"coach_{sub_mode}"
                    system_prompt_template = await _get_prompt(prompt_key)

                    system_text = (
                        f"{system_prompt_template}\n\n"
                        f"ДАННЫЕ АНКЕТЫ: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"{old_plan_context}"
                    )

                    user_instruction = (
                        "[SYSTEM COMMAND] Внимание! Сейчас сгенерируй полный план на основе анкеты.!!!! "
                        
                    )
                    
                    ai_raw = await generate_response(system_text, [{"role": "user", "content": user_instruction}])

                    # 1. Сначала чистим
                    clean_text = clean_html_for_telegram(ai_raw)

                    # 2. Сначала режем, если надо
                    if len(clean_text) > 3800:
                        clean_text = clean_text[:3800]
                        # 3. ВАЖНО: После обрезки прогоняем через балансировщик ЕЩЕ РАЗ, 
                        # чтобы закрыть теги, которые мы только что отрезали
                        clean_text = clean_html_for_telegram(clean_text) + "..."

                    # 4. И только в самом конце оборачиваем в глобальную цитату
                    formatted_plan = f"<blockquote expandable>{clean_text}</blockquote>"

                    await session.execute(
                        update(UserSurvey).where(UserSurvey.id == survey_id).values(
                            ai_recommendation=formatted_plan,
                            current_plan=formatted_plan
                        )
                    )

                    new_history = user.coach_history or []
                    new_history.append({"role": "assistant", "content": formatted_plan}) # В историю тоже с тегом
                    user.coach_history = new_history
                    flag_modified(user, "coach_history")
                    await session.commit()

                    # Отправляем ЮЗЕРУ именно форматированный план
                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"✅ <b>Ваш персональный план готов!</b>\n\n{formatted_plan}\n\n<i>Теперь я всегда на связи. Вы можете написать мне в любой момент и мы скорректируем ваш план. А также, если у вас есть вопросы, я с радостью вам отвечу🙂</i>"
                    })

                # --- СЦЕНАРИЙ 2: ЧАТ (Свободное общение) ---
                # --- СЦЕНАРИЙ 2: ЧАТ (Свободное общение) ---
                elif mode == 'chat':
                    user = await session.get(User, user_id)
                    if not user: return

                    survey = await session.scalar(
                        select(UserSurvey)
                        .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
                        .order_by(desc(UserSurvey.created_at))
                    )
                    
                    # --- ИСПРАВЛЕННЫЙ БЛОК: СНАЧАЛА ПОЛУЧАЕМ ПЕРЕМЕННЫЕ ---
                    current_plan_in_db = survey.current_plan if survey else "План еще не создан."
                    
                    # Получаем ключ промпта без вложенных f-строк
                    current_mode = user.coach_mode or "combined"
                    prompt_key = f"coach_{current_mode}"
                    system_prompt_template = await _get_prompt(prompt_key)

                    system_prompt = (
                        f"{system_prompt_template}\n\n"
                        f"ДАННЫЕ КЛИЕНТА: {json.dumps(survey.answers if survey else {}, ensure_ascii=False)}.\n"
                        f"ТЕКУЩИЙ ПЛАН: {current_plan_in_db}"
                    )

                    # Запрос к ИИ (ЕДИНСТВЕННЫЙ)
                    history_for_api = user.coach_history[-15:] if user.coach_history else []
                    ai_raw = await generate_response(system_prompt, history_for_api)

                    # --- ЛОГИКА ОБРАБОТКИ ОБНОВЛЕНИЯ ---
                    if "[UPDATE_PLAN]" in ai_raw:
                        parts = ai_raw.split("[UPDATE_PLAN]")
                        # chat_comment = parts[0].strip() # Это "вступление", мы его больше НЕ шлем
                        new_plan_content = parts[1].strip() # Это сам план в цитате

                        # Сохраняем в БД только план
                        clean_plan = clean_html_for_telegram(new_plan_content)
                        if survey:
                            survey.current_plan = clean_plan
                            await session.commit()
                        
                        # Для отправки пользователю берем ТОЛЬКО чистый план
                        final_text_for_user = new_plan_content
                    else:
                        final_text_for_user = ai_raw

                    # Чистим весь ответ для Telegram (наша функция clean_html_for_telegram справится)
                    clean_final_text = clean_html_for_telegram(final_text_for_user)
                    if len(clean_final_text) > 3800:
                        clean_final_text = clean_final_text[:3800]
                        # 3. ВАЖНО: После обрезки прогоняем через балансировщик ЕЩЕ РАЗ, 
                        # чтобы закрыть теги, которые мы только что отрезали
                        clean_final_text = clean_html_for_telegram(clean_final_text) + "..."
                        
                    if clean_final_text:
                        # Сохраняем в историю и отправляем
                        new_history = list(user.coach_history or [])
                        new_history.append({'role': 'assistant', 'content': clean_final_text})
                        user.coach_history = new_history
                        flag_modified(user, "coach_history")
                        await session.commit()

                        await send_to_queue("q_notifications", {"user_id": user_id, "text": clean_final_text})

                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                elif mode == 'natal':
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')

                    prompt_template = await _get_prompt("natal")
                    prompt = prompt_template.format(json.dumps(answers, ensure_ascii=False))

                    ai_raw = await generate_response("Ты астролог", [{"role": "user", "content": prompt}])
                    # 1. Сначала чистим
                    clean_text = clean_html_for_telegram(ai_raw)

                    # 2. Сначала режем, если надо
                    if len(clean_text) > 3800:
                        clean_text = clean_text[:3800]
                        # 3. ВАЖНО: После обрезки прогоняем через балансировщик ЕЩЕ РАЗ, 
                        # чтобы закрыть теги, которые мы только что отрезали
                        clean_text = clean_html_for_telegram(clean_text) + "..."

                    # 4. И только в самом конце оборачиваем в глобальную цитату
                    formatted_natal = f"<blockquote expandable>{clean_text}</blockquote>"
                    await session.execute(
                        update(UserSurvey).where(UserSurvey.id == survey_id).values(ai_recommendation=formatted_natal) 
                    )
                    await session.commit()

                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"🌟 <b>Ваша Натальная карта готова:</b>\n\n{formatted_natal}"
                    })

                # --- СЦЕНАРИЙ 4: ТРЕКИНГ ---
                # --- СЦЕНАРИЙ 4: ТРЕКИНГ ---
                elif mode == 'tracking_trigger':
                    user = await session.get(User, user_id)
                    if not user: return

                    survey = await session.scalar(
                        select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach').order_by(desc(UserSurvey.created_at))
                    )
                    current_plan = survey.current_plan if survey else ""
                    answers = survey.answers if survey else {}
                    
                    # Берем актуальную историю
                    history_for_api = list(user.coach_history[-10:]) if user.coach_history else []

                    sub_mode = user.coach_mode or "combined"
                    prompt_key = f"coach_{sub_mode}"
                    system_prompt_template = await _get_prompt(prompt_key)

                    # 2. Теперь собираем финальный системный промпт
                    system_prompt = (
                        f"{system_prompt_template}\n\n" 
                        f"ДАННЫЕ КЛИЕНТА: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"ТЕКУЩИЙ ПЛАН: {current_plan}" # Используем переменную current_plan, созданную выше
                    )
 
                    # Добавляем временную инструкцию для генерации (не сохраняем её в БД!)
                    temp_history = history_for_api + [{
                        "role": "user",
                        "content": "[SYSTEM COMMAND] Инициируй ежедневный трекинг. Коротко и заботливо спроси, как прошел мой день (или проходит, в зависимости от времени суток сейчас по Московскому времени) в контексте цели. Используй эмодзи. Максимум 2 предложения."
                    }]
                    
                    ai_raw = await generate_response(system_prompt, temp_history)
                    clean_text = clean_html_for_telegram(ai_raw)
                    
                    if clean_text:
                        # СОХРАНЯЕМ В ИСТОРИЮ (теперь это начало нового витка диалога)
                        history_for_api.append({'role': 'assistant', 'content': clean_text})
                        user.coach_history = history_for_api
                        flag_modified(user, "coach_history")
                        await session.commit()

                        # Отправляем пользователю
                        await send_to_queue("q_notifications", {
                            "user_id": user_id,
                            "text": f"🔔 <b>Контроль прогресса</b>\n\n{clean_text}",
                            "keyboard": get_coach_menu().model_dump()
                        })

            AI_TASK_PROCESSED.labels(mode=mode, status="success").inc()
            log.info("task_completed", duration=time.time() - start_time)

        except Exception as e:
            log.error("task_failed", error=str(e))
            AI_TASK_PROCESSED.labels(mode=mode, status="error").inc()
            await send_alert(e, context=f"AI Worker CRITICAL ({mode})")
        finally:
            # --- ВСТАВИТЬ ЭТО: Остановка статуса "печатает" ---
            stop_typing.set()
            await typing_task



async def main():
    global bot_instance
    logger.info("service_started", service="ai_worker")
    
    # --- ВСТАВИТЬ ЭТО: Инициализация бота ---
    bot_instance = Bot(
        token=settings.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    connection = await aio_pika.connect_robust(settings.RABBIT_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue("q_ai_generation", durable=True)
    await channel.set_qos(prefetch_count=3)

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            asyncio.create_task(process_task(message))

if __name__ == "__main__":
    start_metrics_server(8004)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())