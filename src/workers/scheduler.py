import asyncio
import json
import sys
import datetime
import pytz
from os.path import abspath, dirname
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, func, and_

# --- НАСТРОЙКА ПУТЕЙ ---
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.config import settings
from src.database.models import User, UserSurvey
from src.database.session import async_session_maker
from src.services.rabbit import send_to_queue
from src.scripts.update_surveys import update_surveys
from src.services.matching import run_daily_matching
from src.services.redis import redis_service
from src.services.llm import generate_response
from src.utils.text import clean_html_for_telegram
from src.bot.states import CoachState

# OBSERVABILITY
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server
from src.utils.alerting import send_alert

async def safe_job_run(job_func, job_id, *args, **kwargs):
    """Обертка для безопасного запуска задач с логированием и алертами."""
    log = logger.bind(job_id=job_id, worker="scheduler")
    try:
        await job_func(*args, **kwargs)
        log.info("job_completed")
    except Exception as e:
        log.error("job_failed", error=str(e))
        await send_alert(e, context=f"Scheduler Job: {job_id}")

# --- ЗАДАЧИ ---

async def check_tracking_times():
    """
    Проверяет часовые пояса пользователей и инициирует общение с ИИ коучем.
    Срабатывает раз в час.
    """
    logger.info("tracking_check_started")
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    bot_id = settings.BOT_TOKEN.get_secret_value().split(':')[1] # Для ключа FSM

    async with async_session_maker() as session:
        # Ищем пользователей с активным трекингом и подпиской
        stmt = select(User).where(
            and_(
                User.is_tracking_enabled == True,
                User.tracking_time.is_not(None),
                User.subscription_expires_at > func.now()
            )
        )
        users = (await session.execute(stmt)).scalars().all()
        
        triggered_count = 0
        for user in users:
            try:
                user_tz = pytz.timezone(user.timezone)
                user_local_time = now_utc.astimezone(user_tz)
                
                # Проверяем совпадение часа
                if user_local_time.hour == user.tracking_time.hour:
                    lock_key = f"tracking_sent:{user.user_id}:{user_local_time.date()}"
                    if await redis_service.get(lock_key):
                        continue

                    # Формируем контекстный запрос к ИИ
                    # Достаем цель из анкеты, если она есть
                    survey = await session.scalar(
                        select(UserSurvey).where(
                            UserSurvey.user_id == user.user_id, 
                            UserSurvey.mode == 'coach'
                        ).order_by(UserSurvey.created_at.desc())
                    )
                    goal = survey.answers.get('goal', 'здоровье') if survey else "здоровье"

                    system_prompt = (
                        f"Ты заботливый ИИ-коуч. Твой клиент поставил цель: {goal}. "
                        f"Сейчас у него {user_local_time.strftime('%H:%M')}. "
                        "Спроси его коротко и вовлекающе, как прошел его день в контексте цели. "
                        "Используй эмодзи. Максимум 2 предложения."
                    )
                    
                    ai_text_raw = await generate_response(system_prompt, "Инициируй ежедневный трекинг.")
                    ai_text = clean_html_for_telegram(ai_text_raw)
                    
                    # Отправляем сообщение в очередь
                    await send_to_queue("q_notifications", {
                        "user_id": user.user_id,
                        "text": f"🔔 <b>Контроль прогресса</b>\n\n{ai_text}"
                    })

                    # ВАЖНО: Принудительно ставим пользователю состояние "chatting" в Redis,
                    # чтобы его ответ сразу попал в AI Worker (режим коуча включен).
                    # Формат ключа aiogram 3: fsm:<bot_id>:<chat_id>:<user_id>:state
                    state_key = f"fsm:{bot_id}:{user.user_id}:{user.user_id}:state"
                    await redis_service.client.set(state_key, CoachState.chatting.state)
                    
                    # Лочим отправку на этот день (на 20 часов)
                    await redis_service.set(lock_key, "1", ex=72000)
                    triggered_count += 1
                    
            except Exception as loop_e:
                logger.error("user_tracking_error", user_id=user.user_id, error=str(loop_e))
                continue

    logger.info("tracking_check_completed", sent_count=triggered_count)

async def update_daily_horoscopes():
    """Генерирует базу гороскопов на день."""
    logger.info("horoscope_update_started")
    from src.services.horoscope import RUS_SIGNS

    for sign_en, sign_ru in RUS_SIGNS.items():
        try:
            prompt = (
                f"Напиши 3 варианта гороскопа на сегодня для знака {sign_ru}. "
                "1 вариант: Вдохновляющий. 2 вариант: Прагматичный. 3 вариант: Осторожный. "
                "Раздели варианты строго символом | без лишнего текста."
            )
            res = await generate_response("Ты профессиональный астролог", prompt)
            # Сохраняем в Redis на 25 часов
            await redis_service.set(f"horoscope_daily:{sign_en}", res, ex=90000)
        except Exception as e:
            logger.error("horoscope_gen_failed", sign=sign_en, error=str(e))

    logger.info("horoscope_update_completed")

# --- ГЛАВНЫЙ ЦИКЛ ---

async def main():
    logger.info("service_started", service="scheduler")
    scheduler = AsyncIOScheduler(timezone="UTC")
    
    # Задачи планировщика
    scheduler.add_job(safe_job_run, 'interval', minutes=30, args=[update_surveys, 'update_surveys'])
    scheduler.add_job(safe_job_run, 'cron', hour=12, minute=0, args=[run_daily_matching, 'dating'])
    scheduler.add_job(safe_job_run, 'cron', hour=8, minute=0, args=[update_daily_horoscopes, 'horoscopes'])
    scheduler.add_job(safe_job_run, 'cron', minute=0, args=[check_tracking_times, 'tracking_hourly'])

    scheduler.start()
    
    # Первичная инициализация при старте воркера
    try:
        await update_surveys()
        # Если в редисе нет гороскопов, генерим сразу
        if not await redis_service.get("horoscope_daily:aries"):
            await update_daily_horoscopes()
    except Exception as e:
        logger.error("scheduler_init_error", error=str(e))

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    start_metrics_server(8003)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("service_stopped")