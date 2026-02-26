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
                    if not user:
                        log.error("user_not_found", user_id=user_id)
                        return

                    old_plan_context = ""
                    if is_adaptation and user and user.coach_history:
                        for msg in reversed(user.coach_history):
                            if msg.get('role') == 'assistant' and "<b>" in msg.get('content', ''):
                                old_plan_context = f"\n\nТЕКУЩИЙ ПЛАН КЛИЕНТА (нужно адаптировать):\n{msg['content']}"
                                break

                    # ИЗМЕНЕНИЕ ЗДЕСЬ: Формируем ключ динамически (coach_nutrition, coach_workout...)
                    prompt_key = f"coach_{sub_mode}"
                    system_prompt_template = await _get_prompt(prompt_key)

                    # Убираем role_instruction, так как роль теперь внутри system_prompt_template
                    system_text = (
                        f"{system_prompt_template}\n\n"
                        f"ДАННЫЕ АНКЕТЫ: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"{old_plan_context}"
                    )

                    user_instruction = (
                        "[SYSTEM COMMAND] Внимание! Сейчас сгенерируй полный план на основе анкеты.!!!! "
                        
                    )
                    
                    ai_raw = await generate_response(system_text, [{"role": "user", "content": user_instruction}])

                    # 1. Сначала чистим ответ ИИ от мусора (ul, li, md)
                    clean_text = clean_html_for_telegram(ai_raw)
                    if len(clean_text) > 3800:
                        clean_text = clean_text[:3800] + "..."
                    # 2. Только ПОТОМ оборачиваем в раскрывающуюся цитату
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
                    
                    # Собираем контекст для ИИ
                    current_plan_in_db = survey.current_plan if survey else "План еще не создан."
                    system_prompt = (
                        f"{await _get_prompt(f'coach_{user.coach_mode or "combined"}')}\n\n"
                        f"ДАННЫЕ КЛИЕНТА: {json.dumps(survey.answers if survey else {}, ensure_ascii=False)}.\n"
                        f"ТЕКУЩИЙ ПЛАН: {current_plan_in_db}"
                    )

                    # Запрос к ИИ (ЕДИНСТВЕННЫЙ)
                    history_for_api = user.coach_history[-15:] if user.coach_history else []
                    ai_raw = await generate_response(system_prompt, history_for_api)

                    # --- ЛОГИКА ОБРАБОТКИ ОБНОВЛЕНИЯ ---
                    if "[UPDATE_PLAN]" in ai_raw:
                        # 1. Извлекаем текст плана, который идет после тега
                        parts = ai_raw.split("[UPDATE_PLAN]")
                        chat_comment = parts[0].strip() # То, что ИИ сказал в чате
                        new_plan_content = parts[1].strip() # Сам новый план

                        # 2. Чистим план и сохраняем в БД
                        clean_plan = clean_html_for_telegram(new_plan_content)
                        if survey:
                            survey.current_plan = clean_plan
                            await session.commit()
                        
                        # 3. Подготавливаем текст для отправки юзеру (убираем только технический тег)
                        final_text_for_user = ai_raw.replace("[UPDATE_PLAN]", "").strip()
                    else:
                        final_text_for_user = ai_raw

                    # Чистим весь ответ для Telegram (наша функция clean_html_for_telegram справится)
                    clean_final_text = clean_html_for_telegram(final_text_for_user)

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
                    clean_text = clean_html_for_telegram(ai_raw)
                    
                    # --- ДОБАВЛЯЕМ ОБЕРТКУ ТУТ ---
                    if len(clean_text) > 3800:
                        clean_text = clean_text[:3800] + "..."
                    
                    formatted_natal = f"<blockquote expandable>{clean_text}</blockquote>"
                    # -----------------------------

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
                    history_for_api = list(user.coach_history) if user.coach_history else []

                    sub_mode = user.coach_mode or "combined"
                    prompt_key = f"coach_{sub_mode}"
                    system_prompt_template = await _get_prompt(prompt_key)

                    # 2. Теперь собираем финальный системный промпт
                    system_prompt = (
                        f"{system_prompt_template}\n\n" 
                        f"ДАННЫЕ КЛИЕНТА: {json.dumps(survey.answers if survey else {}, ensure_ascii=False)}.\n"
                        f"ТЕКУЩИЙ ПЛАН: {current_plan_in_db}"
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