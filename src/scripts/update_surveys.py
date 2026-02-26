# src/scripts/update_surveys.py
import asyncio
import sys
from collections import defaultdict
from os.path import abspath, dirname

sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.services.sheets import fetch_all_data
from src.services.redis import redis_service
from src.utils.logger import logger

async def update_surveys():
    log = logger.bind(task="update_surveys")
    log.info("started")
    
    try:
        # 1. Загружаем чистые данные из Google Таблиц
        questions, prompts import fetch_all_data()
        
        # 2. Группируем вопросы СТРОГО по колонке category из таблицы
        # Если в таблице написано 'coach' — кладем в 'coach', если 'workout' — в 'workout'
        raw_categories = defaultdict(list)
        for q in questions:
            cat = q.get('category', 'common').strip().lower()
            raw_categories[cat].append(q)
            
        # 3. Сохраняем каждую категорию в Redis отдельно
        # Теперь в Redis будут ключи: survey_config:coach, survey_config:workout, и т.д.
        for cat_name, q_list in raw_categories.items():
            await redis_service.set_survey_config(cat_name, q_list)
            log.info("config_saved", category=cat_name, count=len(q_list))

        # 4. Сохраняем промпты
        if prompts:
            for mode, text in prompts.items():
                await redis_service.set_prompt(mode, text)
            log.info("prompts_saved", keys=list(prompts.keys()))
            
        log.info("sync_finished_successfully")
        return True

    except Exception as e:
        log.error("failed", error=str(e))
        raise e

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(update_surveys())