import asyncio
import json
import sys
import time
import aio_pika
from sqlalchemy import select, update, desc
from os.path import abspath, dirname

# Магия путей
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.config import settings
from src.services.llm import generate_response
from src.database.session import async_session_maker
from src.database.models import UserSurvey, User
from src.services.redis import redis_service 
from src.services.rabbit import send_to_queue

# OBSERVABILITY
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server, AI_TASK_PROCESSED, AI_TASK_DURATION
from src.utils.alerting import send_alert
from src.utils.text import clean_html_for_telegram

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
            # Логируем ошибку, если не смогли распарсить JSON
            logger.error("json_decode_error", message_body=message.body)
            return

        user_id = task.get('user_id')
        mode = task.get('mode')
        log = logger.bind(user_id=user_id, mode=mode, worker="ai_worker")
        log.info("task_started")

        try:
            async with async_session_maker() as session:

                # --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ПРОМПТА ---
                async def _get_prompt(prompt_key: str) -> str:
                    prompt_template = await redis_service.get_prompt(prompt_key)
                    if not prompt_template:
                        log.warning("prompt_not_found_in_redis", prompt_key=prompt_key)
                        return "Ты эмпатичный коуч."  # Fallback
                    return prompt_template

                # --- СЦЕНАРИЙ 1: КОУЧ (Первая генерация или Адаптация плана) ---
                if mode == 'coach':
                    sub_mode = task.get('sub_mode')
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')
                    is_adaptation = task.get('is_adaptation', False)

                    user = await session.get(User, user_id)
                    if not user:
                        log.error("user_not_found", user_id=user_id)
                        return # Важно: прерываем обработку, если юзер не найден

                    old_plan_context = ""
                    # Если это адаптация, берем старый план и историю для контекста
                    if is_adaptation and user and user.coach_history:
                        # Ищем самый последний план в истории
                        for msg in reversed(user.coach_history):
                            if msg.get('role') == 'assistant' and "<b>" in msg.get('content', ''):
                                old_plan_context = f"\n\nТЕКУЩИЙ ПЛАН КЛИЕНТА (нужно адаптировать):\n{msg['content']}"
                                break

                    # Формируем системный промпт
                    system_prompt_template = await _get_prompt("coach")
                    system_text = (
                        f"{system_prompt_template}\n"
                        f"РЕЖИМ: {sub_mode}. ДАННЫЕ АНКЕТЫ: {json.dumps(answers, ensure_ascii=False)}."
                        f"{old_plan_context}"
                    )

                    user_instruction = (
                        "[SYSTEM COMMAND] Составь максимально подробный, структурированный и красивый план. "
                        "Используй HTML: <b>, <i>, <ul>, <li>. Сделай акцент на достижимости целей. "
                        "Это твое первое сообщение пользователю в этом режиме."
                    )

                    ai_raw = await generate_response(system_text, [{"role": "user", "content": user_instruction}]) # <---  ИЗМЕНЕНИЕ ЗДЕСЬ
                    clean_text = clean_html_for_telegram(ai_raw)

                    # Обновляем анкету
                    await session.execute(
                        update(UserSurvey).where(UserSurvey.id == survey_id).values(
                            ai_recommendation=clean_text,  # Это поле теперь как "первоначальный план"
                            current_plan=clean_text  # А это будет "живым", обновляемым планом
                        )
                    )

                    # --- ВАЖНО: Инициализируем историю чата ---
                    new_history = user.coach_history or []  # Используем [] даже для сброса
                    new_history.append({"role": "assistant", "content": clean_text})
                    user.coach_history = new_history

                    await session.commit()

                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"✅ <b>Ваш персональный план готов!</b>\n\n{clean_text}\n\n<i>Теперь я всегда на связи. Можешь задавать уточняющие вопросы!</i>"
                    })

                # --- СЦЕНАРИЙ 2: ЧАТ (Свободное общение) ---
                elif mode == 'chat':
                    user = await session.get(User, user_id)
                    if not user:
                        log.error("user_not_found", user_id=user_id)
                        return

                    # Достаем последнюю активную анкету и план
                    survey = await session.scalar(
                        select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach').order_by(desc(UserSurvey.created_at))
                    )
                    current_plan = survey.current_plan if survey else "План еще не создан."
                    answers = survey.answers if survey else {}

                    # --- ГЛАВНОЕ ИЗМЕНЕНИЕ: СОБИРАЕМ КОНТЕКСТ ---
                    history_for_api = user.coach_history or []

                    # Собираем промпт
                    system_prompt_template = await _get_prompt("coach")
                    system_prompt = (
                        f"{system_prompt_template}\n"
                        f"Данные клиента: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"Текущий актуальный план: {current_plan}\n\n"
                        "ИНСТРУКЦИЯ:\n"
                        "1. Отвечай кратко и по делу, основываясь на всей истории диалога.\n"
                        "2. Если клиент просит изменить что-то в плане — подтверди это в диалоге.\n"
                        "3. Если правок накопилось много, в конце сообщения напиши: '[UPDATE_PLAN]'. Я это увижу и обновлю весь текст плана.\n"
                        "4. Используй HTML."
                    )

                    # Ограничиваем размер истории, чтобы не перегружать API
                    if len(history_for_api) > 15:  # Пример: храним последние 15 сообщений
                        history_for_api = history_for_api[-15:]
                    
                    ai_raw = await generate_response(system_prompt, history_for_api)

                    # Проверка: просил ли ИИ обновить план целиком?
                    should_update_full_plan = "[UPDATE_PLAN]" in ai_raw
                    final_reply = ai_raw.replace("[UPDATE_PLAN]", "").strip()
                    clean_text = clean_html_for_telegram(final_reply)
                    log.info("ai_response_text", raw_text=ai_raw, clean_text=clean_text)

                    # Сохраняем ответ ИИ в историю
                    new_history = user.coach_history or []
                    new_history.append({'role': 'assistant', 'content': clean_text})

                    # Если нужно обновить весь план (красивая перегенерация)
                    if should_update_full_plan and survey:
                        # Добавляем в историю "виртуальное" сообщение от юзера, чтобы ИИ понял задачу
                        history_for_api.append({'role': 'user', 'content': '[SYSTEM COMMAND] Отлично, на основе нашего диалога, пожалуйста, составь полную обновленную версию моего плана. Выдай только сам план в HTML без лишних комментариев.'})
                        
                        new_plan_raw = await generate_response(system_prompt, history_for_api)
                        new_plan_clean = clean_html_for_telegram(new_plan_raw)
                        
                        survey.current_plan = new_plan_clean  # Обновляем "живой" план
                        
                        # Также добавляем новый план в историю как сообщение от ассистента
                        new_history.append({'role': 'assistant', 'content': new_plan_clean})
                        
                        # Отправляем отдельным сообщением
                        await send_to_queue("q_notifications", {
                            "user_id": user_id,
                            "text": f"🔄 <b>Договорились! Вот ваш обновленный план:</b>\n\n{new_plan_clean}"
                        })

                    # Сохраняем все изменения в БД
                    user.coach_history = new_history
                    await session.commit()

                    # Отправляем основной ответ пользователю
                    await send_to_queue("q_notifications", {"user_id": user_id, "text": clean_text})

                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                elif mode == 'natal':
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')

                    # Берем промпт из Google Sheets
                    prompt_template = await _get_prompt("natal")
                    prompt = prompt_template.format(json.dumps(answers, ensure_ascii=False))

                    ai_raw = await generate_response("Ты астролог", prompt)
                    clean_text = clean_html_for_telegram(ai_raw)

                    await session.execute(
                        update(UserSurvey).where(UserSurvey.id == survey_id).values(ai_recommendation=clean_text)
                    )
                    await session.commit()

                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"🌟 <b>Ваша Натальная карта готова:</b>\n\n{clean_text}"
                    })

                # --- СЦЕНАРИЙ 4: ТРЕКИНГ (Инициированное ИИ общение) ---
                elif mode == 'tracking_trigger':
                    user = await session.get(User, user_id)
                    if not user:
                        log.error("user_not_found", user_id=user_id)
                        return

                    # Собираем контекст
                    survey = await session.scalar(
                        select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach').order_by(desc(UserSurvey.created_at))
                    )
                    current_plan = survey.current_plan if survey else ""
                    answers = survey.answers if survey else {}
                    history_for_api = user.coach_history or []

                    # Получаем промпт из гугл таблиц (это важно!)
                    system_prompt_template = await _get_prompt("coach")
                    system_prompt = (
                        f"{system_prompt_template}\n"
                        f"Данные клиента: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"Текущий план: {current_plan}"
                    )
                    # Создаем сообщение для трекинга
                    history_for_api.append({
                        "role": "user",
                        "content": "[SYSTEM COMMAND] Коротко и заботливо спроси меня, как прошел мой день в контексте нашей цели. Используй эмодзи."
                    })
                    # Генерируем ответ
                    ai_raw = await generate_response(system_prompt, history_for_api)
                    clean_text = clean_html_for_telegram(ai_raw)
                    # Сохраняем сообщение ИИ в историю
                    new_history = user.coach_history or []
                    new_history.append({'role': 'assistant', 'content': clean_text})
                    user.coach_history = new_history
                    await session.commit()

                    # Отправляем пользователю
                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"🔔 <b>Контроль прогресса</b>\n\n{clean_text}"
                    })

            AI_TASK_PROCESSED.labels(mode=mode, status="success").inc()
            log.info("task_completed", duration=time.time() - start_time)

        except Exception as e:
            log.error("task_failed", error=str(e))
            AI_TASK_PROCESSED.labels(mode=mode, status="error").inc()
            await send_alert(e, context=f"AI Worker CRITICAL ({mode})")

async def main():
    logger.info("service_started", service="ai_worker")
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