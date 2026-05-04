import httpx
import time
import json
import asyncio
from typing import List, Dict, Union, Any, Type, TypeVar
from openai import AsyncOpenAI
from pydantic import BaseModel
from prometheus_client import Histogram

from src.config import settings
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS
from src.utils.llm_logging import log_llm_chat  # Убедитесь, что этот файл создан

# Типизация для структурированного вывода
T = TypeVar("T", bound=BaseModel)

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
    timeout=600.0,
    verify=True 
)

client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY.get_secret_value(),
    http_client=http_client
)

def _prepare_history(history: Union[List[Dict[str, str]], str, Any]) -> List[Dict[str, str]]:
    """Вспомогательный метод для защиты от неверных типов входных данных."""
    if isinstance(history, str):
        try:
            parsed = json.loads(history)
            return parsed if isinstance(parsed, list) else [{"role": "user", "content": str(history)}]
        except json.JSONDecodeError:
            return [{"role": "user", "content": history}]
    elif history is None:
        return []
    elif isinstance(history, list):
        return history
    return []

# --- 1. КЛАССИЧЕСКАЯ ГЕНЕРАЦИЯ (ТЕКСТ) ---
async def generate_response(system_prompt: str, history: Union[List[Dict[str, str]], str, Any], user_id: int = None) -> str:
    """
    Генерация обычного текстового ответа.
    Используется для натальных карт, трекинга и простых запросов.
    """
    start_time = time.time()
    MODEL_NAME = "gpt-4o-mini"
    log = logger.bind(service="llm_client", model=MODEL_NAME, user_id=user_id)

    clean_history = _prepare_history(history)

    try:
        messages_to_send = [{"role": "system", "content": system_prompt}] + clean_history
        
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages_to_send,
            temperature=0.7,
            max_tokens=3000,
            stream=False
        )
        
        content = response.choices[0].message.content
        duration = time.time() - start_time
        LLM_API_DURATION.observe(duration)
        
        if not content:
            log.warning("llm_returned_empty_string", duration=duration)
            return "" 

        # Детальное логирование в файл (через поток, чтобы не блокировать event loop)
        if user_id:
            try:
                await asyncio.to_thread(log_llm_chat, user_id, system_prompt, clean_history, content)
            except Exception as le:
                log.error("llm_log_file_error", error=str(le))

        log.info("llm_request_success", duration=round(duration, 2))
        return content

    except Exception as e:
        log.error("llm_request_failed", error=str(e))
        SYSTEM_ERRORS.labels(service="llm", error_type=type(e).__name__).inc()
        await send_alert(e, context=f"LLM Text Critical ({MODEL_NAME}) | User: {user_id}")
        raise e

# --- 2. СТРУКТУРИРОВАННАЯ ГЕНЕРАЦИЯ (JSON / PYDANTIC) ---
async def generate_structured_response(
    system_prompt: str, 
    history: Union[List[Dict[str, str]], str, Any], 
    response_model: Type[T],
    user_id: int = None
) -> T:
    """
    Генерация строго структурированного ответа по Pydantic схеме.
    Использует OpenAI Structured Outputs (beta.chat.completions.parse).
    """
    start_time = time.time()
    # Метод .parse работает стабильнее всего на моделях gpt-4o-2024-08-06 и gpt-4o-mini
    MODEL_NAME = "gpt-4o-mini" 
    log = logger.bind(service="llm_structured", model=MODEL_NAME, user_id=user_id)

    clean_history = _prepare_history(history)

    try:
        completion = await client.beta.chat.completions.parse(
            model=MODEL_NAME,
            messages=[{"role": "system", "content": system_prompt}] + clean_history,
            response_format=response_model,
            temperature=0.7
        )

        message = completion.choices[0].message

        # Обработка отказа модели (Safety Refusal)
        if message.refusal:
            log.warning("llm_structured_refusal", refusal=message.refusal)
            raise Exception(f"AI Refusal: {message.refusal}")

        parsed_object = message.parsed
        
        duration = time.time() - start_time
        LLM_API_DURATION.observe(duration)

        # Логируем результат в файл
        if user_id:
            try:
                raw_json = parsed_object.model_dump_json(indent=2)
                await asyncio.to_thread(log_llm_chat, user_id, system_prompt, clean_history, raw_json)
            except: pass

        log.info("llm_structured_success", duration=round(duration, 2))
        return parsed_object

    except Exception as e:
        log.error("llm_structured_failed", error=str(e))
        SYSTEM_ERRORS.labels(service="llm_structured", error_type=type(e).__name__).inc()
        await send_alert(e, context=f"LLM Structured Error ({MODEL_NAME}) | User: {user_id}")
        raise e