#src\scripts\init_configs.py

import asyncio
import sys
from os.path import abspath, dirname

sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.database.session import async_session_maker
from src.database.models import SurveyConfig

async def init_configs():
    print("⚙️ Создание базовых конфигураций...")
    
    async with async_session_maker() as session:
        # Создаем заглушки для всех режимов
        configs = [
            SurveyConfig(id=1, mode='diet', version='v1', structure={}, is_current=True),
            SurveyConfig(id=2, mode='trainer', version='v1', structure={}, is_current=True),
            SurveyConfig(id=3, mode='dating', version='v1', structure={}, is_current=True),
            SurveyConfig(id=4, mode='horoscope', version='v1', structure={}, is_current=True),
            SurveyConfig(id=5, mode='natal_chart', version='v1', structure={}, is_current=True),
        ]
        
        for conf in configs:
            await session.merge(conf) # merge создаст или обновит
        
        await session.commit()
        print("💾 Конфигурации (ID 1-5) успешно сохранены!")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_configs())