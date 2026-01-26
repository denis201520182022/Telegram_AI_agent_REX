import asyncio
import sys
import datetime
import pytz # Нужно для работы с зонами
from os.path import abspath, dirname
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, func, and_

# --- НАСТРОЙКА ПУТЕЙ ---
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

# --- ИМПОРТЫ ---
from src.database.models import User, UserSurvey
from src.database.session import async_session_maker
from src.services.rabbit import send_to_queue
from src.scripts.update_surveys import update_surveys
from src.services.matching import run_daily_matching
from src.services.redis import redis_service
from src.services.llm import generate_response
from src.utils.text import clean_html_for_telegram

# OBSERVABILITY
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server
from src.utils.alerting import send_alert

async def safe_job_run(job_func, job_id, *args, **kwargs):
    log = logger.bind(job_id=job_id, worker="scheduler")
    try:
        await job_func(*args, **kwargs)
        log.info("job_completed")
    except Exception as e:
        log.error("job_failed", error=str(e))
        await send_alert(e, context=f"Scheduler: {job_id}")

# --- ЗАДАЧИ ---

async def check_tracking_times():
    """
    Запускается каждый час (в 00 минут).
    Проверяет, у кого из юзеров сейчас "Время трекинга".
    """
    logger.info("tracking_check_started")
    
    # 1. Текущее время в UTC
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    async with async_session_maker() as session:
        # Берем пользователей, у которых включен трекинг и подписка активна
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
                # 2. Получаем таймзону юзера
                user_tz = pytz.timezone(user.timezone)
                
                # 3. Переводим UTC время в локальное время юзера
                user_local_time = now_utc.astimezone(user_tz)
                
                # 4. Сравниваем ЧАСЫ
                # Если у юзера 20:15, а tracking_time 20:00 -> совпадение
                if user_local_time.hour == user.tracking_time.hour:
                    
                    # Чтобы не спамить (если вдруг скрипт запустится дважды за час),
                    # можно использовать Redis ключ с TTL 1 час.
                    lock_key = f"tracking_sent:{user.user_id}:{user_local_time.date()}"
                    if await redis_service.get(lock_key):
                        continue
                        
                    # 5. Формируем сообщение от ИИ
                    # Нам нужно достать имя и контекст, чтобы вопрос был "живым"
                    # Для скорости просто отправим задачу в очередь AI Worker, 
                    # пусть он сам сгенерирует вопрос "Как дела?".
                    
                    # Но AI Worker ждет входящее сообщение от юзера для чата.
                    # Поэтому тут мы схитрим: мы отправим задачу с флагом "init_tracking"
                    
                    # (Для простоты MVP: генерируем тут промпт и шлем в очередь уведомлений)
                    
                    system_prompt = (
                        "Ты заботливый коуч. Твой клиент живет в часовом поясе, где сейчас вечер (или время отчета). "
                        "Спроси у него коротко (1-2 предложения), как прошел день, что он ел и тренировался ли. "
                        "Будь дружелюбен."
                    )
                    
                    # Генерируем текст вопроса
                    ai_text_raw = await generate_response(system_prompt, "Сгенерируй вопрос для трекинга.")
                    ai_text = clean_html_for_telegram(ai_text_raw)
                    
                    # Шлем юзеру
                    await send_to_queue("q_notifications", {
                        "user_id": user.user_id,
                        "text": f"🔔 <b>Трекинг</b>\n\n{ai_text}"
                    })
                    
                    # Ставим лок на 20 часов
                    await redis_service.set(lock_key, "1", ex=72000)
                    triggered_count += 1
                    
            except Exception as loop_e:
                logger.error("user_tracking_error", user_id=user.user_id, error=str(loop_e))
                continue

    logger.info("tracking_check_completed", sent_count=triggered_count)

# --- ЗАПУСК ---
async def main():
    logger.info("service_started", service="scheduler")
    scheduler = AsyncIOScheduler(timezone="UTC") # Сам шедулер работает в UTC
    
    # 1. Обновление вопросов (раз в 30 мин)
    scheduler.add_job(safe_job_run, 'interval', minutes=30, args=[update_surveys, 'update_surveys'])
    
    # 2. Дейтинг (раз в день в 12:00 UTC) - можно оставить фиксированно
    scheduler.add_job(safe_job_run, 'cron', hour=12, minute=0, args=[run_daily_matching, 'dating'])
    
    # 3. ТРЕКИНГ ПОЯСОВ (Каждый час ровно в 00 минут)
    scheduler.add_job(safe_job_run, 'cron', minute=0, args=[check_tracking_times, 'tracking_hourly'])

    scheduler.start()
    
    # Первичная загрузка вопросов
    try:
        await update_surveys()
    except: 
        pass

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    start_metrics_server(8003)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())