# src/bot/middlewares/activity.py
import time
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from src.services.redis import redis_service

class ActivityMiddleware(BaseMiddleware):
    """
    Middleware для автоматического обновления времени 
    последней активности пользователя в Redis.
    """
    async def __call__(self, handler, event: TelegramObject, data: dict):
        # Извлекаем пользователя из события (сообщение или колбэк)
        user = data.get("event_from_user")
        
        if user and not user.is_bot:
            # Формируем ключ, например: last_activity:12345678
            key = f"last_activity:{user.id}"
            # Сохраняем текущий Unix-timestamp
            # Мы используем прямой доступ к клиенту redis через твой сервис
            await redis_service.client.set(key, int(time.time()))
        
        # Передаем управление следующему обработчику
        return await handler(event, data)