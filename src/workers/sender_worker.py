import asyncio
import json
import sys
import aio_pika
from os.path import abspath, dirname
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

# Магия путей
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))
from src.utils.text import clean_html_for_telegram
from src.config import settings
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

# --- OBSERVABILITY (Логи, Метрики, Алерты) ---
from src.utils.logger import logger
from src.utils.metrics import start_metrics_server, MESSAGES_SENT, SYSTEM_ERRORS
from src.utils.alerting import send_alert

async def process_notification(message: aio_pika.IncomingMessage, bot: Bot):
    """
    Обрабатывает одно сообщение из очереди RabbitMQ и отправляет его в Telegram.
    """
    async with message.process(): # Автоматический ack/nack при выходе из контекста
        try:
            data = json.loads(message.body)
            user_id = data['user_id']
            raw_text = data.get('text', '') # Берем текст из очереди
            
            # ФИНАЛЬНАЯ ОЧИСТКА ПЕРЕД ТЕЛЕГРАМОМ
            # Это спасет бота от падения, если ИИ протащил <ul> или другие теги
            text = clean_html_for_telegram(raw_text)
            photo = data.get('photo')
            
            # Десериализуем клавиатуру из JSON обратно в объект aiogram
            keyboard_data = data.get('keyboard')
            keyboard = None

            if keyboard_data:
                # Проверяем, что внутри: если есть ключ 'inline_keyboard' — это инлайн кнопки
                if "inline_keyboard" in keyboard_data:
                    keyboard = InlineKeyboardMarkup.model_validate(keyboard_data)
                # Если есть ключ 'keyboard' — это обычное меню (Reply)
                elif "keyboard" in keyboard_data:
                    keyboard = ReplyKeyboardMarkup.model_validate(keyboard_data)

            log = logger.bind(user_id=user_id, worker="sender")
            log.info("sending_message_attempt")

            # --- ОТПРАВКА ---
            if photo:
                # Если передан URL или file_id фото
                await bot.send_photo(
                    chat_id=user_id, 
                    photo=photo, 
                    caption=text, 
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
            else:
                await bot.send_message(
                    chat_id=user_id, 
                    text=text, 
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )

            # Метрика успеха
            MESSAGES_SENT.labels(status="success").inc()
            log.info("message_sent_successfully")

        except TelegramRetryAfter as e:
            # Если словили Flood Limit (слишком много сообщений)
            MESSAGES_SENT.labels(status="rate_limit").inc()
            logger.warning("telegram_rate_limit", retry_after=e.retry_after, user_id=user_id)
            
            # Возвращаем в очередь и ждем
            await message.nack(requeue=True)
            await asyncio.sleep(e.retry_after) 

        except (TelegramForbiddenError, TelegramBadRequest) as e:
            # Юзер заблокировал бота или ID неверный — удаляем задачу, не переповторяем
            MESSAGES_SENT.labels(status="blocked_or_invalid").inc()
            logger.error("sending_aborted", error=str(e), user_id=user_id)
            # Сообщение будет подтверждено (ack) через context manager и уйдет из очереди

        except Exception as e:
            # Внутренняя ошибка сервера или сети
            MESSAGES_SENT.labels(status="failed").inc()
            SYSTEM_ERRORS.labels(service="sender", error_type=type(e).__name__).inc()
            
            logger.error("sending_failed_critical", error=str(e), user_id=user_id)
            await send_alert(e, context=f"Sender Worker Error for user {user_id}")
            
            # Возвращаем в очередь для повторной попытки
            await message.nack(requeue=True)

async def main():
    logger.info("service_started", service="sender")
    
    # Инициализация бота
    bot = Bot(
        token=settings.BOT_TOKEN.get_secret_value(), 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Подключение к RabbitMQ
    connection = await aio_pika.connect_robust(settings.RABBIT_URL)
    channel = await connection.channel()
    
    # Очередь уведомлений
    queue = await channel.declare_queue("q_notifications", durable=True)
    
    # Настраиваем QoS (Quality of Service) - сколько сообщений воркер может брать одновременно
    await channel.set_qos(prefetch_count=10)

    # Используем итератор для бесконечного цикла прослушивания
    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            # Запускаем отправку асинхронно, чтобы один "медленный" юзер не тормозил всю очередь
            asyncio.create_task(process_notification(message, bot))

if __name__ == "__main__":
    # Запуск сервера метрик для Prometheus
    start_metrics_server(8001)
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("service_stopped")