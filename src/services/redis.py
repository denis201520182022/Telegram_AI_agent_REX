import json
from typing import Optional, Any
from redis.asyncio import Redis, from_url
from redis.exceptions import RedisError
from src.config import settings

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS

# --- ЕДИНСТВЕННЫЙ ЭКЗЕМПЛЯР КЛИЕНТА ---
redis_client: Redis = from_url(settings.REDIS_URL, decode_responses=True)

class RedisService:
    """
    Класс-сервис для инкапсуляции всей логики работы с Redis.
    Включает обработку ошибок и логирование.
    """
    def __init__(self, client: Redis):
        self.client = client
        self.log = logger.bind(service="redis")

    async def _safe_get(self, key: str) -> Optional[str]:
        """Внутренний метод для безопасного чтения."""
        try:
            return await self.client.get(key)
        except RedisError as e:
            self.log.error("redis_get_failed", key=key, error=str(e))
            SYSTEM_ERRORS.labels(service="redis", error_type=type(e).__name__).inc()
            # Redis часто бывает критичным, но иногда можно пережить сбой
            # Для надежности можно не слать алерт на каждый get, но залогировать обязательно
            return None

    async def _safe_set(self, key: str, value: Any, ex: int = None):
        """Внутренний метод для безопасной записи."""
        try:
            await self.client.set(key, value, ex=ex)
        except RedisError as e:
            self.log.error("redis_set_failed", key=key, error=str(e))
            SYSTEM_ERRORS.labels(service="redis", error_type=type(e).__name__).inc()
            await send_alert(e, context="Redis Set Operation")
            raise e

    # --- Работа с анкетами ---
    async def get_survey_config(self, mode: str) -> Optional[list]:
        """
        Возвращает список вопросов.
        Раньше mode был ключом. Теперь мы сохраним все вопросы под ключом 'all_questions',
        а фильтровать будем уже в коде бота.
        Но для совместимости оставим аргумент mode, если вдруг захотим разделить dating.
        """
        # Если mode == 'coach', мы достаем полный список вопросов 'coach_questions'
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
        # config теперь это list
        key = f"survey_questions:{mode}"
        await self._safe_set(key, json.dumps(config))
        
    # --- Работа с промптами ---
    async def get_prompt(self, mode: str) -> Optional[str]:
        return await self._safe_get(f"prompt:{mode}")
        
    async def set_prompt(self, mode: str, text: str):
        await self._safe_set(f"prompt:{mode}", text)

    # --- Работа с гороскопами ---
    async def get_horoscope(self, sign: str) -> Optional[str]:
        return await self._safe_get(f"horoscope:{sign}")

    async def set_horoscope(self, sign: str, text: str):
        await self._safe_set(f"horoscope:{sign}", text, ex=86400) 

    # --- Общие операции ---
    async def get(self, key: str) -> Optional[str]:
        return await self._safe_get(key)

    async def set(self, key: str, value: Any, ex: int = None):
        await self._safe_set(key, value, ex=ex)

# --- ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР СЕРВИСА ---
redis_service = RedisService(redis_client)