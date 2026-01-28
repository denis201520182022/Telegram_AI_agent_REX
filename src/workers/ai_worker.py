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
from src.database.models import UserSurvey, ChatMessage, User
from src.services.redis import redis_service 
from src.services.rabbit import send_to_queue

# OBSERVABILITY
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server, AI_TASK_PROCESSED, AI_TASK_DURATION
from src.utils.alerting import send_alert
from src.utils.text import clean_html_for_telegram

async def build_chat_context(session, user_id: int, limit: int = 12) -> str:
    """Собирает историю сообщений для контекста ИИ."""
    stmt = select(ChatMessage).where(ChatMessage.user_id == user_id).order_by(desc(ChatMessage.created_at)).limit(limit)
    result = await session.execute(stmt)
    history = result.scalars().all()
    
    # Разворачиваем историю (от старых к новым)
    history = history[::-1]
    
    context_str = ""
    for msg in history:
        role = "Клиент" if msg.role == 'user' else "Коуч"
        context_str += f"{role}: {msg.content}\n"
    
    return context_str

async def process_task(message: aio_pika.IncomingMessage):
    """
    Основной обработчик задач ИИ. 
    Режимы: coach (план/адаптация), chat (свободное общение), natal (натальная карта).
    """
    async with message.process():
        start_time = time.time()
        
        try:
            task = json.loads(message.body)
        except json.JSONDecodeError:
            return 

        user_id = task.get('user_id')
        mode = task.get('mode') 
        log = logger.bind(user_id=user_id, mode=mode, worker="ai_worker")
        log.info("task_started")

        try:
            async with async_session_maker() as session:
                
                # --- СЦЕНАРИЙ 1: КОУЧ (Генерация или Адаптация плана) ---
                if mode == 'coach':
                    sub_mode = task.get('sub_mode') # nutrition, workout, combined
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')
                    is_adaptation = task.get('is_adaptation', False)

                    # Если это адаптация, достаем старый план для контекста
                    old_plan_context = ""
                    if is_adaptation:
                        old_survey = await session.scalar(
                            select(UserSurvey).where(
                                UserSurvey.user_id == user_id, 
                                UserSurvey.id != survey_id
                            ).order_by(desc(UserSurvey.created_at))
                        )
                        if old_survey and old_survey.current_plan:
                            old_plan_context = f"\n\nТЕКУЩИЙ ПЛАН КЛИЕНТА (нужно адаптировать):\n{old_survey.current_plan}"

                    # Формируем системный промпт
                    prompt_template = await redis_service.get_prompt("coach")
                    if not prompt_template:
                        prompt_template = "Ты профессиональный коуч по биохакингу, питанию и спорту."

                    system_text = (
                        f"{prompt_template}\n"
                        f"РЕЖИМ: {sub_mode}. ДАННЫЕ АНКЕТЫ: {json.dumps(answers, ensure_ascii=False)}."
                        f"{old_plan_context}"
                    )
                    
                    user_instruction = (
                        "Составь максимально подробный, структурный и красивый план. "
                        "Используй HTML: <b>, <i>, <ul>, <li>. Сделай акцент на достижимости целей."
                    )

                    ai_raw = await generate_response(system_text, user_instruction)
                    clean_text = clean_html_for_telegram(ai_raw)
                    
                    # Сохраняем план в БД
                    await session.execute(
                        update(UserSurvey).where(UserSurvey.id == survey_id).values(
                            ai_recommendation=clean_text,
                            current_plan=clean_text
                        )
                    )
                    
                    # Добавляем в историю чата как "assistant"
                    session.add(ChatMessage(user_id=user_id, role='assistant', content=clean_text))
                    await session.commit()

                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"✅ <b>Ваш персональный план готов!</b>\n\n{clean_text}\n\n<i>Теперь я всегда на связи. Можешь задавать уточняющие вопросы!</i>"
                    })

                # --- СЦЕНАРИЙ 2: ЧАТ (Свободное общение) ---
                elif mode == 'chat':
                    user_text = task.get('text')
                    
                    # Достаем последнюю активную анкету и план
                    survey = await session.scalar(
                        select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach').order_by(desc(UserSurvey.created_at))
                    )
                    current_plan = survey.current_plan if survey else "План еще не создан."
                    answers = survey.answers if survey else {}
                    
                    history_context = await build_chat_context(session, user_id)
                    
                    system_prompt = (
                        f"Ты — эмпатичный Коуч. Твои рекомендации основаны на данных клиента: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"ТЕКУЩИЙ ПЛАН:\n{current_plan}\n\n"
                        "ИНСТРУКЦИЯ:\n"
                        "1. Отвечай кратко и по делу.\n"
                        "2. Если клиент просит изменить что-то в плане — подтверди это в диалоге.\n"
                        "3. Если правок много, в конце сообщения напиши: '[UPDATE_PLAN]'. Я это увижу и обновлю весь текст плана.\n"
                        "4. Используй HTML."
                    )
                    
                    user_content = f"ИСТОРИЯ:\n{history_context}\nКЛИЕНТ: {user_text}"
                    
                    ai_raw = await generate_response(system_prompt, user_content)
                    
                    # Проверка: просил ли ИИ обновить план целиком?
                    should_update_full_plan = "[UPDATE_PLAN]" in ai_raw
                    final_reply = ai_raw.replace("[UPDATE_PLAN]", "").strip()
                    clean_text = clean_html_for_telegram(final_reply)

                    # Сохраняем в историю
                    session.add(ChatMessage(user_id=user_id, role='assistant', content=clean_text))
                    
                    # Если нужно обновить весь план (красивая перегенерация)
                    if should_update_full_plan and survey:
                        update_prompt = f"На основе диалога выше составь ПОЛНУЮ обновленную версию плана. Выдай только текст нового плана в HTML."
                        new_plan_raw = await generate_response(system_prompt, update_prompt)
                        new_plan_clean = clean_html_for_telegram(new_plan_raw)
                        
                        survey.current_plan = new_plan_clean
                        await send_to_queue("q_notifications", {
                            "user_id": user_id,
                            "text": f"🔄 <b>Обновленный план:</b>\n\n{new_plan_clean}"
                        })

                    await session.commit()
                    await send_to_queue("q_notifications", {"user_id": user_id, "text": clean_text})

                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                elif mode == 'natal':
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')
                    
                    prompt = "Ты профессиональный ведический астролог. Составь подробную натальную карту по данным: {}. Опиши сильные стороны и предназначение.".format(json.dumps(answers, ensure_ascii=False))
                    
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