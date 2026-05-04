"""Модуль конфигурации для Telegram-бота."""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    """Конфигурация Telegram-бота."""
    token: str
    admin_ids: list[int]


@dataclass
class DatabaseConfig:
    """Конфигурация базы данных."""
    url: str


@dataclass
class QwenConfig:
    """Конфигурация Qwen3-Max API."""
    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    model: str = "qwen3-max"


@dataclass
class SchedulerConfig:
    """Конфигурация планировщика."""
    daily_reminder_hour: int = 20
    daily_reminder_minute: int = 0
    partner_suggestion_hour: int = 12
    partner_suggestion_minute: int = 0
    weekly_report_day: int = 6  # Воскресенье
    weekly_report_hour: int = 10
    weekly_report_minute: int = 0
    cache_cleanup_hour: int = 2
    cache_cleanup_minute: int = 0


@dataclass
class AppConfig:
    """Основная конфигурация приложения."""
    bot: BotConfig
    database: DatabaseConfig
    qwen: QwenConfig
    scheduler: SchedulerConfig


def load_config() -> AppConfig:
    """Загружает конфигурацию из переменных окружения."""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("BOT_TOKEN не найден в переменных окружения")
    
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
    
    database_url = os.getenv("DATABASE_URL", "sqlite:///bot.db")
    
    qwen_api_key = os.getenv("QWEN_API_KEY")
    if not qwen_api_key:
        raise ValueError("QWEN_API_KEY не найден в переменных окружения")
    
    return AppConfig(
        bot=BotConfig(
            token=bot_token,
            admin_ids=admin_ids
        ),
        database=DatabaseConfig(
            url=database_url
        ),
        qwen=QwenConfig(
            api_key=qwen_api_key
        ),
        scheduler=SchedulerConfig()
    )
```