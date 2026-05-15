# Инструкция по работе с Proxy в проекте REX

В проекте используется прокси-сервер (Squid) для обхода ограничений и обеспечения стабильного доступа к API Telegram и OpenAI. 

## 1. Настройка переменных окружения (.env)
Все параметры прокси должны быть прописаны в файле `.env`:
```env
SQUID_PROXY_HOST=your_proxy_ip
SQUID_PROXY_PORT=3128
SQUID_PROXY_USER=proxy_user
SQUID_PROXY_PASSWORD=proxy_password
```

## 2. Использование в Telegram боте (aiogram)
Для работы бота через прокси необходимо создать `AiohttpSession` и передать её в объект `Bot`.

```python
from aiogram.client.session.aiohttp import AiohttpSession
from src.config import settings

# Формируем URL прокси с авторизацией
proxy_url = (
    f"http://{settings.SQUID_PROXY_USER}:{settings.SQUID_PROXY_PASSWORD}@"
    f"{settings.SQUID_PROXY_HOST}:{settings.SQUID_PROXY_PORT}"
)

# Создаем сессию
session = AiohttpSession(proxy=proxy_url)

# Инициализируем бота с этой сессией
bot = Bot(
    token=settings.BOT_TOKEN.get_secret_value(),
    session=session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
```

## 3. Использование для HTTP-запросов (httpx / OpenAI)
Для библиотек, использующих `httpx` (например, OpenAI SDK), прокси настраивается через `AsyncClient`.

```python
import httpx
from openai import AsyncOpenAI
from src.config import settings

proxy_url = (
    f"http://{settings.SQUID_PROXY_USER}:{settings.SQUID_PROXY_PASSWORD}@"
    f"{settings.SQUID_PROXY_HOST}:{settings.SQUID_PROXY_PORT}"
)

# Создаем клиент с прокси
http_client = httpx.AsyncClient(
    proxy=proxy_url,
    timeout=600.0,
    verify=True 
)

# Передаем клиент в OpenAI
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY.get_secret_value(),
    http_client=http_client
)
```

## 4. Важные замечания
- **Безопасность:** Никогда не хардкодьте учетные данные прокси. Используйте `src.config.settings`.
- **Протокол:** Используйте `http://` даже если прокси поддерживает HTTPS, так как библиотека сама поднимет CONNECT-туннель.
- **Таймауты:** Для LLM запросов рекомендуется устанавливать увеличенный таймаут (например, `timeout=600.0`), так как генерация текста может занимать много времени.
- **Docker:** Внутри Docker-сети прокси доступен по имени сервиса, если он запущен в том же `docker-compose.yml`.
