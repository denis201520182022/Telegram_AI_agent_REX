import httpx
import time
from openai import AsyncOpenAI
from prometheus_client import Histogram
from src.config import settings

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS

# Метрика времени ответа
LLM_API_DURATION = Histogram(
    'rex_llm_api_request_duration_seconds',
    'Time spent waiting for LLM response'
)

# --- НАСТРОЙКА ПРОКСИ ---
# Формируем URL для прокси из наших настроек
proxy_url = (
    f"http://{settings.SQUID_PROXY_USER}:{settings.SQUID_PROXY_PASSWORD}@"
    f"{settings.SQUID_PROXY_HOST}:{settings.SQUID_PROXY_PORT}"
)

# Создаем асинхронный HTTP клиент, который принудительно идет через прокси
# Мы используем mounts для перехвата всех http и https запросов
http_client = httpx.AsyncClient(
    proxy=proxy_url,
    timeout=60.0,
    verify=True # Проверка SSL сертификатов
)

# Клиент Groq (через OpenAI интерфейс), теперь с нашим проксированным http_client
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY.get_secret_value(),
    base_url="https://api.groq.com/openai/v1",
    http_client=http_client
)

async def generate_response(system_prompt: str, user_content: str) -> str:
    """
    Генерация ответа через Groq (Llama-3.3).
    Запрос идет через SQUID прокси для обхода блокировок 403.
    """
    start_time = time.time()
    
    # Очень быстрая и мощная модель
    MODEL_NAME = "llama-3.3-70b-versatile" 

    log = logger.bind(service="llm_client", provider="groq", model=MODEL_NAME)

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.7,
            max_tokens=2000, # Немного увеличим для подробных планов
            stream=False
        )
        
        duration = time.time() - start_time
        LLM_API_DURATION.observe(duration)
        
        return response.choices[0].message.content

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)
        
        # Если прокси не работает или данные неверны, тут может вылететь ошибка подключения
        log.error("llm_request_failed", error=error_msg, duration=duration)
        SYSTEM_ERRORS.labels(service="llm", error_type=type(e).__name__).inc()
        
        # Оповещаем админа о сбое (возможно прокси "упал")
        await send_alert(e, context=f"LLM Service via Proxy ({MODEL_NAME})")
        
        raise e