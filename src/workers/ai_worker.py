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

from src.services.llm import generate_structured_response
from src.services.schemas import CoachResponse
from src.utils.renderer import render_plan_to_html
from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from src.services.llm import generate_structured_response
from src.services.schemas import CoachResponse
from src.utils.renderer import render_plan_to_html


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

                

                # --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ФОРМАТИРОВАНИЯ ПРОМПТА ---
                async def _format_system_prompt(template: str, answers: dict, current_plan_json: any = None) -> str:
                    if not template: return ""
                    # 1. Вставляем продукты
                    if "{rex_product}" in template:
                        product_content = await redis_service.get_prompt("rex_product")
                        template = template.replace("{rex_product}", product_content or "[Список продуктов]")
                    # 2. Вставляем переменные анкеты
                    if answers:
                        for key, value in answers.items():
                            template = template.replace(f"{{{key}}}", str(value))
                    # 3. Вставляем JSON текущего плана для "памяти" ИИ
                    if current_plan_json:
                        template += f"\n\nТЕКУЩИЙ ПЛАН КЛИЕНТА (в формате JSON для контекста):\n{json.dumps(current_plan_json, ensure_ascii=False)}"
                    return template

                # --- УНИФИЦИРОВАННЫЙ СЦЕНАРИЙ: КОУЧ И ЧАТ ---
                if mode in ['coach', 'chat']:
                    user = await session.get(User, user_id)
                    if not user: return

                    # Определяем ID анкеты (для coach берем из таска, для чата ищем последнюю)
                    survey_id = task.get('survey_id')
                    if mode == 'chat':
                        survey = await session.scalar(
                            select(UserSurvey)
                            .where(UserSurvey.user_id == user_id, UserSurvey.mode == 'coach')
                            .order_by(desc(UserSurvey.created_at))
                        )
                        survey_id = survey.id if survey else None
                    else:
                        survey = await session.get(UserSurvey, survey_id)

                    answers = survey.answers if survey else {}
                    # Достаем чистый JSON плана из новой колонки БД
                    current_plan_json = getattr(survey, 'current_plan_json', None) if survey else None
                    
                    # Подготовка системного промпта
                    sub_mode = user.coach_mode or task.get('sub_mode', 'combined')
                    prompt_key = f"coach_{sub_mode}"
                    template = await _get_prompt(prompt_key)
                    system_text = await _format_system_prompt(template, answers, current_plan_json)

                    # Подготовка истории
                    history = list(user.coach_history[-15:]) if user.coach_history else []
                    if mode == 'coach':
                        history.append({"role": "user", "content": "[SYSTEM COMMAND] Сгенерируй первый полный персональный план на основе моих данных."})
                    elif task.get('text'):
                        # Если это чат, добавляем накопленный текст пользователя
                        history.append({"role": "user", "content": task.get('text')})

                    # ЗАПРОС К ИИ (СТРУКТУРИРОВАННЫЙ)
                    response_obj = await generate_structured_response(
                        system_prompt=system_text,
                        history=history,
                        response_model=CoachResponse,
                        user_id=user_id
                    )

                    # --- 1. ОТПРАВКА ТЕКСТОВОГО ОТВЕТА (ЧАТ) ---
                    full_chat_text = ""
                    if response_obj.message_to_user:
                        full_chat_text = response_obj.message_to_user
                        # Дробим, если текст чата вдруг гигантский
                        chat_chunks = [full_chat_text[i:i+3800] for i in range(0, len(full_chat_text), 3800)]
                        for chunk in chat_chunks:
                            # Обязательно балансируем теги в каждом куске
                            safe_chunk = clean_html_for_telegram(chunk)
                            await send_to_queue("q_notifications", {"user_id": user_id, "text": safe_chunk})
                            # Пауза 0.5 - 1 сек между кусками текста, чтобы они не перемешались
                            await asyncio.sleep(0.8)
                    # --- 2. ОБРАБОТКА И ОТПРАВКА ПЛАНА ---
                    rendered_plan_for_history = ""
                    if response_obj.plan_update_required and response_obj.plan_sections:
                        # Сохраняем в БД: чистый JSON
                        if survey:
                            survey.current_plan_json = [section.model_dump() for section in response_obj.plan_sections]
                            flag_modified(survey, "current_plan_json")
                            # HTML для БД сгенерируем один раз целиком
                            full_plan_html = render_plan_to_html(response_obj.plan_sections)
                            survey.current_plan = f"<blockquote expandable>{full_plan_html}</blockquote>"
                            await session.commit()

                        # СМАРТ-ДРОБЛЕНИЕ ПО БЛОКАМ
                        current_chunk_sections = []
                        current_length = 0
                        messages_to_send = []

                        for section in response_obj.plan_sections:
                            # Рендерим одну секцию, чтобы оценить её размер
                            rendered_sec = render_plan_to_html([section])
                            # Очищаем её (чтобы не было двойного экранирования позже)
                            # Но так как render_plan_to_html уже создает теги <b> и т.д., 
                            # мы должны быть уверены, что clean_html_for_telegram их не убьет.
                            # На самом деле лучше очищать поля секции ДО рендеринга.
                            
                            sec_len = len(rendered_sec)

                            if current_length + sec_len > 3500 and current_chunk_sections:
                                # Отправляем накопленное
                                messages_to_send.append(render_plan_to_html(current_chunk_sections))
                                current_chunk_sections = [section]
                                current_length = sec_len
                            else:
                                current_chunk_sections.append(section)
                                current_length += sec_len + 2 # +2 для \n\n

                        if current_chunk_sections:
                            messages_to_send.append(render_plan_to_html(current_chunk_sections))

                        # Отправка сформированных блоков
                        await asyncio.sleep(1.2)
                        for i, msg_text in enumerate(messages_to_send):
                            prefix = "✅ <b>Ваш план:</b>\n\n" if i == 0 else "<b>Продолжение плана:</b>\n\n"
                            # ВАЖНО: Мы уже имеем HTML с тегами b, i, code. 
                            # Нам нужно только сбалансировать их и закрыть возможные дыры.
                            safe_p_chunk = clean_html_for_telegram(msg_text)
                            wrapped_chunk = f"{prefix}<blockquote expandable>{safe_p_chunk}</blockquote>"
                            
                            await send_to_queue("q_notifications", {"user_id": user_id, "text": wrapped_chunk})
                            await asyncio.sleep(1.0)
                        
                        rendered_plan_for_history = f"\n\n[ОБНОВЛЕННЫЙ ПЛАН]:\n{render_plan_to_html(response_obj.plan_sections)}"

                    # --- 3. ОБНОВЛЕНИЕ ИСТОРИИ ДИАЛОГА ---
                    new_history = list(user.coach_history or [])
                    # В историю пишем и ответ, и контент плана, чтобы ИИ видел, что он выдал
                    combined_assistant_msg = full_chat_text + rendered_plan_for_history
                    if combined_assistant_msg:
                        new_history.append({'role': 'assistant', 'content': combined_assistant_msg})
                        user.coach_history = new_history[-20:] # Храним последние 20
                        flag_modified(user, "coach_history")
                        await session.commit()

                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                # --- СЦЕНАРИЙ 3: НАТАЛЬНАЯ КАРТА ---
                elif mode == 'natal':
                    answers = task.get('answers')
                    survey_id = task.get('survey_id')

                    prompt_template = await _get_prompt("natal")
                    prompt = prompt_template.format(json.dumps(answers, ensure_ascii=False))

                    ai_raw = await generate_response("Ты астролог", [{"role": "user", "content": prompt}], user_id=user_id)
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

                    # Собираем финальный системный промпт
                    system_prompt = (
                        f"{system_prompt_template}\n\n" 
                        f"ДАННЫЕ КЛИЕНТА: {json.dumps(answers, ensure_ascii=False)}.\n"
                        f"ТЕКУЩИЙ ПЛАН: {current_plan}"
                    )
 
                    # Добавляем временную инструкцию для генерации
                    temp_history = history_for_api + [{
                        "role": "user",
                        "content": "[SYSTEM COMMAND] Инициируй ежедневный трекинг. Коротко и заботливо спроси у собеседника, как прошел его день, получилось ли придерживаться плана, может хочет внести какие то изменения в план и так далее. Отталкивайся от контекста диалога и цели клиента. Будь вариативен, не повторяйся. Используй уместные разнообразные эмодзи. Максимум 2 предложения."
                    }]
                    
                    # Используем СТРУКТУРИРОВАННУЮ генерацию, чтобы не парсить JSON руками
                    response_obj = await generate_structured_response(
                        system_prompt=system_prompt,
                        history=temp_history,
                        response_model=CoachResponse,
                        user_id=user_id
                    )
                    
                    clean_text = clean_html_for_telegram(response_obj.message_to_user)
                    
                    if clean_text:
                        # СОХРАНЯЕМ В ИСТОРИЮ
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