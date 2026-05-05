# --- СТАДИЯ СБОРКИ (BUILDER) ---
FROM python:3.11-slim-bookworm as builder

WORKDIR /app

# Настройка зеркала для ускорения (Яндекс)
RUN sed -i 's/deb.debian.org/mirror.yandex.ru/g' /etc/apt/sources.list

# Устанавливаем зависимости для сборки бинарных пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Обновляем pip и копируем требования
COPY requirements.txt .

# Собираем wheels (бинарные пакеты) для всех зависимостей. 
# УБРАН флаг --no-deps, чтобы подтянулись абсолютно все нужные пакеты.
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

# Устанавливаем системные библиотеки (runtime), нужные для работы базы и прокси
RUN sed -i 's/deb.debian.org/mirror.yandex.ru/g' /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Копируем собранные пакеты из стадии builder
COPY --from=builder /app/wheels /wheels

# Устанавливаем пакеты локально.
# --no-index говорит не лезть в интернет, а --find-links берет всё из нашей папки.
RUN pip install -U pip && \
    pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && \
    rm -rf /wheels

# Копируем исходный код проекта
COPY . .

# Выставляем права доступа
RUN chown -R appuser:appgroup /app

# Переключаемся на безопасного пользователя
USER appuser

# Команда запуска обычно переопределяется в docker-compose.yml, 
# но здесь мы оставляем заглушку или основную точку входа.
CMD ["python", "-m", "src.bot.main"]