# --- СТАДИЯ СБОРКИ (BUILDER) ---
FROM python:3.11-slim-bookworm as builder

WORKDIR /app

# Оптимизация зеркал для скорости
RUN sed -i 's/deb.debian.org/mirror.yandex.ru/g' /etc/apt/sources.list.d/debian.sources

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Собираем wheels во временную папку
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt


# --- ФИНАЛЬНАЯ СТАДИЯ (RUNNER) ---
FROM python:3.11-slim-bookworm

WORKDIR /app

# Настройки Python для Docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Создаем не-root пользователя для безопасности
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Устанавливаем только необходимые runtime зависимости (libpq для работы с Postgres)
RUN sed -i 's/deb.debian.org/mirror.yandex.ru/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Копируем собранные пакеты из builder
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# Копируем проект
COPY . .

# Меняем владельца файлов на не-root пользователя
RUN chown -R appuser:appgroup /app

# Переключаемся на безопасного пользователя
USER appuser

# Используем init для корректной обработки сигналов (SIGTERM, SIGINT)
# (Для этого в docker-compose добавим init: true)
