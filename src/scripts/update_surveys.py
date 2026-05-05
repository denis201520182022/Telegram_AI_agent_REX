import asyncio
import sys
from collections import defaultdict
from os.path import abspath, dirname

# Настройка путей
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.services.sheets import fetch_all_data
from src.services.google_docs import fetch_prompts_from_doc  # ДОБАВИЛИ ИМПОРТ
from src.services.redis import redis_service
from src.utils.logger import logger

async def update_surveys():
    log = logger.bind(task="update_surveys")
    log.info("sync_started")
    
    try:
        # 1. Загружаем ВОПРОСЫ из Google Sheets (Лист Questions)
        # Теперь получаем просто объект, а не распаковываем его
        questions = await fetch_all_data()
        
        # 2. Загружаем ПРОМПТЫ из Google Docs
        # Вызываем ваш второй сервис
        prompts = await fetch_prompts_from_doc()
        
        if not questions:
            log.error("sync_failed_no_questions_found")
            return

        # 3. Группируем вопросы по категориям
        raw_categories = defaultdict(list)
        for q in questions:
            cat_name = q.get('category', 'common').strip().lower()
            raw_categories[cat_name].append(q)

        # 4. Сохраняем вопросы в Redis
        for cat_name, q_list in raw_categories.items():
            if q_list:
                await redis_service.set_survey_config(cat_name, q_list)
                log.info("category_saved", category=cat_name, count=len(q_list))

        # 5. Сохраняем системные промпты в Redis
        if prompts:
            for mode, text in prompts.items():
                await redis_service.set_prompt(mode, text)
            log.info("prompts_saved", keys=list(prompts.keys()))
        else:
            log.warn("no_prompts_found_in_google_doc")
            
        log.info("sync_finished_successfully")
        return True

    except Exception as e:
        log.error("sync_failed", error=str(e))
        raise e

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    print("🚀 Запуск синхронизации (Таблицы + Документы)...")
    asyncio.run(update_surveys())