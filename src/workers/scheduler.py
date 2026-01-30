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
from src.bot.keyboards.coach import get_coach_menu
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
# src/workers/scheduler.py

async def check_tracking_times():
    """
    Проверяет часовые пояса пользователей и инициирует общение с ИИ коучем.
    """
    logger.info("tracking_check_started")
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # Не забудь вытащить bot_id для ключа FSM
    bot_id = settings.BOT_TOKEN.get_secret_value().split(':')[0]


    async with async_session_maker() as session:
        # ЗАМЕНИТЕ ВАШУ СТРОКУ С (...) НА ЭТУ:
        stmt = select(User).where(
            and_(
                User.is_tracking_enabled == True,
                User.tracking_time.is_not(None),
                User.subscription_expires_at > func.now()
            )
        )
        # -------------------------------------------
        
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

                    # Отправляем задачу в AI воркер
                    await send_to_queue("q_ai_generation", {
                        "user_id": user.user_id,
                        "mode": "tracking_trigger" 
                    })

                    # Принудительно ставим состояние chatting в Redis
                    state_key = f"fsm:{user.user_id}:{user.user_id}:state"
                    await redis_service.client.set(state_key, CoachState.chatting.state)
                    
                    # Лочим отправку на этот день
                    await redis_service.set(lock_key, "1", ex=72000)
                    triggered_count += 1
                    
            except Exception as loop_e:
                logger.error("user_tracking_error", user_id=user.user_id, error=str(loop_e))
                continue

    logger.info("tracking_check_completed", sent_count=triggered_count)

async def update_daily_horoscopes():
    """Генерирует базу гороскопов на день, используя промпт из Google Sheets."""
    logger.info("horoscope_update_started")
    from src.services.horoscope import RUS_SIGNS

    # 1. Получаем шаблон промпта из Redis (ключ 'horoscope')
    # Тот самый текст, который вы написали в таблице
    base_prompt = await redis_service.get_prompt("horoscope")
    
    if not base_prompt:
        logger.warning("horoscope_prompt_not_found_in_redis_using_fallback")
        base_prompt = "Напиши гороскоп на сегодня для знака {sign}. Используй HTML."

    for sign_en, sign_ru in RUS_SIGNS.items():
        try:
            # 2. Подставляем имя знака в промпт из таблицы
            # В таблице у вас написано: "...прогноз на сегодня для знака <b>{sign}</b>"
            final_user_prompt = base_prompt.replace("{sign}", sign_ru)

            # 3. Делаем запрос к ИИ
            # Передаем как список сообщений для совместимости с новой логикой llm.py
            res = await generate_response(
                "Ты профессиональный астролог", 
                [{"role": "user", "content": final_user_prompt}]
            )
            
            if res:
                # Очищаем от лишнего мусора, если ИИ его добавил
                clean_res = clean_html_for_telegram(res)
                # Сохраняем в Redis на 25 часов
                await redis_service.set(f"horoscope_daily:{sign_en}", clean_res, ex=90000)
                
        except Exception as e:
            logger.error("horoscope_gen_failed", sign=sign_en, error=str(e))

    logger.info("horoscope_update_completed")

# --- ГЛАВНЫЙ ЦИКЛ ---

async def main():
    logger.info("service_started", service="scheduler")
    scheduler = AsyncIOScheduler(timezone="UTC")
    
    # Задачи планировщика
    scheduler.add_job(safe_job_run, 'interval', minutes=3, args=[update_surveys, 'update_surveys'])
    scheduler.add_job(safe_job_run, 'cron', hour=12, minute=0, args=[run_daily_matching, 'dating'])
    scheduler.add_job(safe_job_run, 'cron', hour=8, minute=0, args=[update_daily_horoscopes, 'horoscopes'])
    scheduler.add_job(safe_job_run, 'cron', minute=0, args=[check_tracking_times, 'tracking_hourly'])
    # Для теста можно поставить проверку раз в минуту
    scheduler.add_job(safe_job_run, 'interval', minutes=1, args=[check_tracking_times, 'tracking_test'])

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