import json
from typing import Optional, Any
from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError
from src.config import settings

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS

# --- ИНИЦИАЛИЗАЦИЯ КЛИЕНТА ---
# decode_responses=True позволяет получать строки (str) вместо байтов (bytes)
redis_client: Redis = from_url(settings.REDIS_URL, decode_responses=True)

class RedisService:
    """
    Сервис для работы с кэшем Redis.
    Используется для хранения:
    1. Конфигураций анкет (из Google Sheets)
    2. Промптов для ИИ
    3. Ежедневных гороскопов
    4. Состояний FSM и временных блокировок (locks)
    """
    def __init__(self, client: Redis):
        self.client = client
        self.log = logger.bind(service="redis")

    async def _safe_get(self, key: str) -> Optional[str]:
        """Безопасное чтение ключа с логированием ошибок."""
        try:
            return await self.client.get(key)
        except RedisError as e:
            self.log.error("redis_get_failed", key=key, error=str(e))
            SYSTEM_ERRORS.labels(service="redis", error_type=type(e).__name__).inc()
            return None

    async def _safe_set(self, key: str, value: Any, ex: int = None):
        """Безопасная запись ключа с возможностью установки TTL (времени жизни)."""
        try:
            await self.client.set(key, value, ex=ex)
        except RedisError as e:
            self.log.error("redis_set_failed", key=key, error=str(e))
            SYSTEM_ERRORS.labels(service="redis", error_type=type(e).__name__).inc()
            await send_alert(e, context=f"Redis SET Error: {key}")
            raise e

    # --- АНКЕТЫ И КОНФИГИ ---
    async def get_survey_config(self, mode: str) -> Optional[list]:
        """Получает список вопросов для конкретного режима (coach, dating, natal)."""
        key = f"survey_questions:{mode}"
        data = await self._safe_get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                self.log.error("redis_json_decode_error", key=key)
                return None
        return None

    async def set_survey_config(self, mode: str, config: list):
        """Сохраняет список вопросов в кэш."""
        key = f"survey_questions:{mode}"
        await self._safe_set(key, json.dumps(config, ensure_ascii=False))
        
    # --- ПРОМПТЫ ИИ ---
    async def get_prompt(self, mode: str) -> Optional[str]:
        """Получает системную инструкцию для ИИ."""
        return await self._safe_get(f"prompt:{mode}")
        
    async def set_prompt(self, mode: str, text: str):
        """Сохраняет промпт в кэш."""
        await self._safe_set(f"prompt:{mode}", text)

    # --- ГОРОСКОПЫ ---
    async def get_horoscope(self, sign: str) -> Optional[str]:
        """Получает варианты гороскопа на сегодня для знака зодиака."""
        return await self._safe_get(f"horoscope_daily:{sign}")

    async def set_horoscope(self, sign: str, text: str):
        """Сохраняет гороскоп (обычно на 24-25 часов)."""
        await self._safe_set(f"horoscope_daily:{sign}", text, ex=90000) 

    # --- ОБЩИЕ ОПЕРАЦИИ ---
    async def get(self, key: str) -> Optional[str]:
        """Универсальный метод получения данных."""
        return await self._safe_get(key)

    async def set(self, key: str, value: Any, ex: int = None):
        """Универсальный метод установки данных."""
        await self._safe_set(key, value, ex=ex)

    async def delete(self, key: str):
        """Удаление ключа."""
        try:
            await self.client.delete(key)
        except RedisError as e:
            self.log.error("redis_delete_failed", key=key, error=str(e))

# --- ЭКСПОРТ ЕДИНОГО ЭКЗЕМПЛЯРА ---
redis_service = RedisService(redis_client)