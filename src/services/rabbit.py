import json
import aio_pika
from src.config import settings

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS

async def send_to_queue(queue_name: str, data: dict):
    """
    Отправляет задачу в формате JSON в указанную очередь RabbitMQ.
    
    Используется для:
    - q_notifications: отправка сообщений пользователям через sender_worker.
    - q_ai_generation: отправка запросов на генерацию текста через ai_worker.
    
    Обеспечивает PERSISTENT (стойкую) доставку сообщений.
    """
    # Контекстное логирование для отслеживания пути задачи
    log = logger.bind(service="rabbitmq", queue=queue_name)
    
    try:
        # connect_robust автоматически переподключается при разрыве соединения
        connection = await aio_pika.connect_robust(settings.RABBIT_URL)
        
        async with connection:
            channel = await connection.channel()
            
            # Объявляем очередь. durable=True значит, что очередь переживет рестарт RabbitMQ
            await channel.declare_queue(queue_name, durable=True)
            
            # Сериализуем данные в JSON
            message_body = json.dumps(data, ensure_ascii=False).encode()
            
            # Публикуем сообщение в стандартный exchange
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=message_body, 
                    # PERSISTENT заставляет RabbitMQ записывать сообщение на диск
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key=queue_name
            )
            
            log.info("message_published_success", data_keys=list(data.keys()))

    except Exception as e:
        # Критическое логирование и метрики
        log.error("message_publish_failed", error=str(e))
        SYSTEM_ERRORS.labels(service="rabbitmq", error_type=type(e).__name__).inc()
        
        # Оповещение администратора, так как сбой очереди останавливает работу ИИ и рассылок
        await send_alert(e, context=f"RabbitMQ Publisher Error ({queue_name})")
        
        # Пробрасываем исключение, чтобы вызывающий метод (например, хендлер) 
        # мог сообщить пользователю о технической ошибке
        raise e