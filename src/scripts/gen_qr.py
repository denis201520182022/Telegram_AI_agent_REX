#src\scripts\gen_qr.py



import asyncio
import csv
import secrets
import sys
from os.path import abspath, dirname

# Магия путей, чтобы видеть папку src
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from sqlalchemy import insert
from src.database.session import async_session_maker
from src.database.models import QRCode

# Настройки
BATCH_ID = "BATCH_001_TEST" # Номер партии
COUNT = 100                 # Сколько кодов генерируем (для теста хватит 100, потом поставишь 1_000_000)
BOT_USERNAME = "REX_Health_bot" # Твой юзернейм бота (без @)

async def generate_codes():
    print(f"🚀 Начинаю генерацию {COUNT} QR-кодов для партии {BATCH_ID}...")
    
    codes_data = []
    csv_rows = []
    
    # 1. Генерируем данные в памяти
    for _ in range(COUNT):
        # Генерируем случайный токен (8 байт = 11 символов base64, url-safe)
        token = secrets.token_urlsafe(8) 
        
        # Данные для БД
        codes_data.append({
            "code_hash": token,
            "batch_id": BATCH_ID,
            "is_active": True
        })
        
        # Данные для CSV
        link = f"https://t.me/{BOT_USERNAME}?start={token}"
        csv_rows.append([link, token])

    print("✅ Генерация в памяти завершена. Записываю в БД...")

    # 2. Массовая вставка в Postgres (Bulk Insert)
    # Мы используем Core (insert), а не ORM, потому что это в 100 раз быстрее для больших объемов
    async with async_session_maker() as session:
        try:
            stmt = insert(QRCode).values(codes_data)
            await session.execute(stmt)
            await session.commit()
            print("💾 Успешно сохранено в PostgreSQL!")
        except Exception as e:
            print(f"❌ Ошибка при записи в БД: {e}")
            await session.rollback()
            return

    # 3. Сохранение в CSV
    filename = f"qr_codes_{BATCH_ID}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Full Link", "Token"]) # Заголовки
        writer.writerows(csv_rows)
    
    print(f"📄 Файл {filename} создан. Можно отправлять в типографию.")

if __name__ == "__main__":
    # Запуск асинхронной функции
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(generate_codes())