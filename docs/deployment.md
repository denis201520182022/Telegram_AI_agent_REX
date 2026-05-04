# Развертывание и настройка

## Требования
- Docker и Docker Compose.
- Зарегистрированный Telegram бот (полученный у @BotFather).
- API ключи OpenAI или Groq.
- Сервисный аккаунт Google (для работы с таблицами).

## Настройка окружения
Создайте файл `.env` в корне проекта на основе `.env.example`:

```env
# Telegram
BOT_TOKEN=your_token
ADMIN_IDS=123456789,987654321

# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=secure_password
POSTGRES_DB=rex_db
DB_URL=postgresql+asyncpg://postgres:secure_password@postgres_db:5432/rex_db

# Redis & RabbitMQ
REDIS_URL=redis://:redis_password@redis:6379/0
REDIS_PASSWORD=redis_password
RABBIT_URL=amqp://guest:guest@rabbitmq:5672/

# AI
OPENAI_API_KEY=sk-...
QWEN_API_KEY=... (optional)

# Monitoring
GRAFANA_PASSWORD=admin_password
```

## Запуск
Система запускается одной командой:
```bash
docker-compose up -d --build
```

Это поднимет:
1. Базу данных и пулер (Postgres, PgBouncer).
2. Очереди и кэш (RabbitMQ, Redis).
3. Основной процесс бота (`bot_polling`).
4. Воркеры (`worker_ai`, `worker_sender`, `worker_scheduler`).
5. Весь стек мониторинга (Grafana, Prometheus, Loki, Promtail, cAdvisor).

## Мониторинг
После запуска Grafana доступна по адресу: `http://your-server-ip:4000`.
- Логин: `admin`
- Пароль: из переменной `GRAFANA_PASSWORD`.

В Grafana уже настроены (или могут быть импортированы) дашборды для отслеживания:
- Количества активных пользователей.
- Скорости ответов AI.
- Очередей RabbitMQ.
- Состояния системных ресурсов.

## Обновление базы данных
При изменении моделей данных используйте Alembic:
```bash
docker-compose exec bot_polling alembic revision --autogenerate -m "description"
docker-compose exec bot_polling alembic upgrade head
```
