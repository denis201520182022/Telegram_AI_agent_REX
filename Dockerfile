# --- СТАДИЯ СБОРКИ (BUILDER) ---
FROM python:3.11-slim-bookworm as builder

WORKDIR /app

# Устанавливаем зависимости для сборки бинарных пакетов (gcc, postgres и т.д.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Обновляем pip и копируем требования
COPY requirements.txt .

# Собираем wheels для всех зависимостей (включая все вложенные зависимости)
RUN pip install -U pip && \
    pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt


# --- ФИНАЛЬНАЯ СТАДИЯ (RUNNER) ---
FROM python:3.11-slim-bookworm

WORKDIR /app

# Настройки Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Создаем не-root пользователя
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Устанавливаем системные библиотеки для работы (libpq5 для Postgres, nc для healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Копируем собранные пакеты из стадии builder
COPY --from=builder /app/wheels /wheels

# Устанавливаем пакеты локально без обращения в интернет
RUN pip install -U pip && \
    pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && \
    rm -rf /wheels

# Копируем проект
COPY . .

# Выставляем права
RUN chown -R appuser:appgroup /app

# Переключаемся на безопасного пользователя
USER appuser

# Точка входа (может быть переопределена в docker-compose)
CMD ["python", "-m", "src.bot.main"]