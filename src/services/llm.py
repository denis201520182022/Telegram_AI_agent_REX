import httpx
import time
import json
from typing import List, Dict
from openai import AsyncOpenAI
from prometheus_client import Histogram
from src.config import settings

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS

LLM_API_DURATION = Histogram(
    'rex_llm_api_request_duration_seconds',
    'Time spent waiting for LLM response'
)

# --- НАСТРОЙКА ПРОКСИ ---
proxy_url = (
    f"http://{settings.SQUID_PROXY_USER}:{settings.SQUID_PROXY_PASSWORD}@"
    f"{settings.SQUID_PROXY_HOST}:{settings.SQUID_PROXY_PORT}"
)

http_client = httpx.AsyncClient(
    proxy=proxy_url,
    timeout=60.0,
    verify=True 
)

client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY.get_secret_value(),
    base_url="https://api.groq.com/openai/v1",
    http_client=http_client
)

async def generate_response(system_prompt: str, history: List[Dict[str, str]]) -> str:
    """
    Генерация ответа через Groq (Llama-3.3).
    Принимает системный промпт и историю сообщений (список словарей).
    """
    start_time = time.time()
    MODEL_NAME = "llama-3.3-70b-versatile" 
    
    # Логгер с контекстом
    log = logger.bind(service="llm_client", provider="groq", model=MODEL_NAME)

    try:
        # Собираем итоговый список сообщений
        messages_to_send = [{"role": "system", "content": system_prompt}] + history
        
        # ЛОГИРУЕМ ТО, ЧТО ОТПРАВЛЯЕМ (Для отладки "пустых" ответов)
        # log.debug("llm_request_payload", messages_count=len(messages_to_send), last_message=history[-1] if history else None)

        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_to_send,
            temperature=0.7,
            max_tokens=2000,
            stream=False
        )
        
        duration = time.time() - start_time
        LLM_API_DURATION.observe(duration)
        
        content = response.choices[0].message.content
        
        # Если API вернул None или пустоту
        if not content:
            log.warning("llm_returned_empty_string", duration=duration)
            return "" # Возвращаем пустую строку, обработаем в worker

        return content

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)
        
        log.error("llm_request_failed", error=error_msg, duration=duration)
        SYSTEM_ERRORS.labels(service="llm", error_type=type(e).__name__).inc()
        await send_alert(e, context=f"LLM Service via Proxy ({MODEL_NAME})")
        
        raise e