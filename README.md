# Останавливаем и удаляем всё (включая данные БД)
docker-compose down -v

# Собираем и запускаем в фоне
docker-compose up -d --build

# 1. Создаем файл миграции
docker exec rex_bot bash -c "DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres_db:5432/rex_db alembic revision --autogenerate -m 'init_structure2'"

# 2. Применяем миграцию (создаем таблицы)
docker exec rex_bot bash -c "DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres_db:5432/rex_db alembic upgrade head"

# 1. Создаем базовые конфиги в таблице survey_configs
docker exec rex_bot python -m src.scripts.init_configs

# 2. Скачиваем вопросы из Google Sheets и кладем в Redis
# (Убедись, что Google-таблица заполнена и сервисный аккаунт имеет к ней доступ)
docker exec rex_bot python -m src.scripts.update_surveys



# Генерируем коды
docker exec rex_bot python -m src.scripts.gen_qr

# Читаем созданный CSV файл прямо в консоль (ищем там ссылку t.me/...)
docker exec rex_bot cat qr_codes_BATCH_001_TEST.csv



docker logs -f bot_polling
docker logs -f rex_worker_ai
docker logs -f rex_worker_sender
docker logs -f rex_worker_sender rex_worker_ai rex_bot


docker exec -it rex_postgres psql -U postgres -d rex_db

Запуск дейтинга:
docker exec -it rex_bot python -c "import asyncio; from src.services.matching import run_daily_matching; asyncio.run(run_daily_matching())"

Запуск трекинга:
docker exec -it rex_bot python -c "import asyncio; from src.workers.scheduler import check_tracking_times; asyncio.run(check_tracking_times())"

docker exec -it rex_redis redis-cli KEYS "tracking_sent:1975808643:2026-01-30" | xargs -I {} docker exec -it rex_redis redis-cli DEL {}

docker exec -it rex_bot python -c "import asyncio; from src.services.rabbit import send_to_queue; asyncio.run(send_to_queue('q_ai_generation', {'user_id': 1975808643, 'mode': 'tracking_trigger'}))"








