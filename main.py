"""Главный модуль Telegram-бота REX Health & Sports Community."""

import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import load_config
from handlers import (
    auth_handlers,
    dietitian_handlers,
    trainer_handlers,
    dating_handlers,
    horoscope_handlers,
    daily_handlers,
    admin_handlers
)
from middlewares import (
    DatabaseMiddleware,
    UserMiddleware
)
from services.scheduler import setup_scheduler
from database import create_tables


async def main():
    """Главная функция запуска бота."""
    # Загрузка конфигурации
    config = load_config()

    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)

    # Инициализация бота и диспетчера
    bot = Bot(token=config.bot.token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Создание таблиц в базе данных
    await create_tables()

    # Настройка маршрутов
    routers = [
        auth_handlers.router,
        dietitian_handlers.router,
        trainer_handlers.router,
        dating_handlers.router,
        horoscope_handlers.router,
        daily_handlers.router,
        admin_handlers.router
    ]

    for router in routers:
        dp.include_router(router)

    # Middlewares
    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())

    # Настройка команд меню
    commands = [
        BotCommand(command="/start", description="Начать работу с ботом"),
        BotCommand(command="/help", description="Помощь и подсказки"),
        BotCommand(command="/edit", description="Изменить анкету"),
        BotCommand(command="/stats", description="Статистика"),
        BotCommand(command="/stop", description="Остановить уведомления")
    ]

    await bot.set_my_commands(commands)

    # Запуск планировщика задач
    scheduler = setup_scheduler(bot, config)
    scheduler.start()

    logger.info("Бот запущен")

    # Запуск поллинга
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
```