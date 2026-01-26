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

async def build_chat_context(session, user_id: int, limit: int = 10) -> str:
    """Собирает последние N сообщений для контекста."""
    stmt = select(ChatMessage).where(ChatMessage.user_id == user_id).order_by(desc(ChatMessage.created_at)).limit(limit)
    history = (await session.execute(stmt)).scalars().all()
    
    # Разворачиваем, так как доставали с конца
    history = history[::-1]
    
    context_str = ""
    for msg in history:
        role = "Клиент" if msg.role == 'user' else "Коуч"
        context_str += f"{role}: {msg.content}\n"
    
    return context_str

async def process_task(message: aio_pika.IncomingMessage):
    async with message.process():
        start_time = time.time()
        
        try:
            task = json.loads(message.body)
        except json.JSONDecodeError:
            return 

        user_id = task.get('user_id')
        mode = task.get('mode') # 'coach' (генерация плана) или 'chat' (общение)
        
        log = logger.bind(user_id=user_id, mode=mode, worker="ai_worker")
        log.info("task_started")

        try:
            async with async_session_maker() as session:
                # --- СЦЕНАРИЙ 1: ГЕНЕРАЦИЯ ПЕРВОГО ПЛАНА ---
                if mode == 'coach':
                    sub_mode = task.get('sub_mode')
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')

                    # 1. Берем промпт
                    prompt_template = await redis_service.get_prompt("coach")
                    if not prompt_template:
                        prompt_template = "Ты коуч. Составь план для клиента: {answers}"

                    system_text = prompt_template.format(answers=json.dumps(answers, ensure_ascii=False), goal=answers.get('goal', 'Здоровье'))
                    
                    user_instruction = (
                        "Составь подробный план (Питание и/или Тренировки) на основе моих данных.\n"
                        "Используй HTML теги (<b>, <li>). Структурируй ответ."
                    )

                    # 2. Генерация
                    ai_raw = await generate_response(system_text, user_instruction)
                    clean_text = clean_html_for_telegram(ai_raw)
                    
                    # 3. Сохраняем план в БД (current_plan) и результат анкеты
                    stmt = update(UserSurvey).where(UserSurvey.id == survey_id).values(
                        ai_recommendation=clean_text,
                        current_plan=clean_text
                    )
                    await session.execute(stmt)
                    
                    # 4. Сохраняем в историю сообщений (чтобы контекст начался с этого плана)
                    # Сообщение от бота
                    first_msg = ChatMessage(user_id=user_id, role='assistant', content=clean_text)
                    session.add(first_msg)
                    await session.commit()

                    # 5. Отправляем юзеру
                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": f"✅ <b>План готов!</b>\n\n{clean_text}\n\n<i>Теперь мы можем общаться свободно. Пиши мне!</i>"
                    })

                # --- СЦЕНАРИЙ 2: ЧАТ С КОУЧЕМ ---
                elif mode == 'chat':
                    user_text = task.get('text')
                    
                    # 1. Достаем контекст (Анкету и Текущий план)
                    # Ищем последнюю актуальную анкету
                    survey = await session.scalar(
                        select(UserSurvey).where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach').order_by(desc(UserSurvey.created_at)).limit(1)
                    )
                    
                    current_plan = survey.current_plan if survey else "План еще не составлен."
                    answers = survey.answers if survey else {}
                    
                    # 2. Собираем историю переписки
                    history_context = await build_chat_context(session, user_id, limit=10)
                    
                    # 3. Формируем мощный системный промпт
                    system_prompt = (
                        f"Ты — опытный Коуч по здоровью (Питание и Спорт).\n"
                        f"Данные клиента: {json.dumps(answers, ensure_ascii=False)}\n"
                        f"ТЕКУЩИЙ ПЛАН КЛИЕНТА:\n{current_plan}\n\n"
                        f"Твоя задача: отвечать на вопросы, поддерживать, корректировать план если просят.\n"
                        f"Отвечай кратко, емко, используй HTML (<b>, <i>)."
                    )
                    
                    user_content = f"ИСТОРИЯ ДИАЛОГА:\n{history_context}\n\nКЛИЕНТ ПИШЕТ: {user_text}"
                    
                    # 4. Генерация ответа
                    ai_raw = await generate_response(system_prompt, user_content)
                    clean_text = clean_html_for_telegram(ai_raw)
                    
                    # 5. Сохраняем ответ бота в историю
                    bot_msg = ChatMessage(user_id=user_id, role='assistant', content=clean_text)
                    session.add(bot_msg)
                    await session.commit()
                    
                    # 6. Отправляем ответ
                    await send_to_queue("q_notifications", {
                        "user_id": user_id,
                        "text": clean_text
                    })

            AI_TASK_PROCESSED.labels(mode=mode, status="success").inc()
            log.info("task_completed", duration=time.time() - start_time)

        except Exception as e:
            log.error("task_failed", error=str(e))
            AI_TASK_PROCESSED.labels(mode=mode, status="error").inc()
            await send_alert(e, context=f"AI Worker ({mode})")
            # Ack, чтобы не зацикливать
            pass

async def main():
    logger.info("service_started", service="ai_worker")
    connection = await aio_pika.connect_robust(settings.RABBIT_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue("q_ai_generation", durable=True)
    await channel.set_qos(prefetch_count=5)

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            asyncio.create_task(process_task(message))

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())