# src/services/llm.py

import httpx
import time
import json
from typing import List, Dict, Union, Any # Добавил Union и Any
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
    #base_url="https://api.groq.com/openai/v1",
    http_client=http_client
)

# Обновил аннотацию типов, чтобы Python знал, что может прийти что угодно
async def generate_response(system_prompt: str, history: Union[List[Dict[str, str]], str, Any]) -> str:
    """
    Генерация ответа через Groq (Llama-3.3).
    Автоматически исправляет формат history, если пришла строка.
    """
    start_time = time.time()
    MODEL_NAME = "gpt-4o-mini"
    #MODEL_NAME = "llama-3.3-70b-versatile" 
    
    log = logger.bind(service="llm_client", provider="groq", model=MODEL_NAME)

    # --- БЛОК ЗАЩИТЫ (FIX TYPE ERROR) ---
    # Проверяем, что нам пришло в history, и приводим к списку
    if isinstance(history, str):
        try:
            # Если это JSON-строка (например '[{"role":...}]'), парсим её
            parsed_history = json.loads(history)
            if isinstance(parsed_history, list):
                history = parsed_history
            else:
                # Если распарсилось не в список, а в dict или что-то еще
                history = [{"role": "user", "content": str(history)}]
        except json.JSONDecodeError:
            # Если это просто текст, оборачиваем его в структуру сообщения
            history = [{"role": "user", "content": history}]
            
    elif history is None:
        history = []
        
    elif not isinstance(history, list):
        # Если пришел какой-то мусор (не список и не строка)
        history = []
    # ------------------------------------

    try:
        # Теперь history гарантированно список, и сложение не упадет
        messages_to_send = [{"role": "system", "content": system_prompt}] + history
        
        # Логируем payload для отладки (чтобы видеть, что уходит)
        log.info("llm_payload_debug", payload_preview=str(messages_to_send)[:500])

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
        duration = time.time() - start_time # Пересчитываем или используем существующий
        
        if not content:
            log.warning("llm_returned_empty_string", duration=duration)
            return "" 

        # ДОБАВЬ ЭТУ СТРОКУ:
        log.info("llm_request_success", duration=round(duration, 2), tokens_limit=2000)

        return content

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)
        
        log.error("llm_request_failed", error=error_msg, duration=duration)
        SYSTEM_ERRORS.labels(service="llm", error_type=type(e).__name__).inc()
        await send_alert(e, context=f"LLM Service via Proxy ({MODEL_NAME})") # Можно временно отключить алерт, чтобы не спамил
        
        raise e