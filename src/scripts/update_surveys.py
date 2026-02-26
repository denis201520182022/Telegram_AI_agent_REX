# src/scripts/update_surveys.py
import asyncio
import sys
from collections import defaultdict
from os.path import abspath, dirname

# Настройка путей, чтобы скрипт видел папку src
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.services.sheets import fetch_all_data
from src.services.redis import redis_service
from src.utils.logger import logger

async def update_surveys():
    """
    Синхронизирует данные из Google Таблиц и раскладывает их 
    по отдельным категориям в Redis.
    """
    log = logger.bind(task="update_surveys")
    log.info("sync_started")
    
    try:
        # 1. Загружаем данные из Google Sheets
        # fetch_all_data возвращает (questions, prompts)
        questions, prompts = await fetch_all_data()
        
        if not questions:
            log.error("sync_failed_no_questions_found")
            return

        # 2. Группируем вопросы по категориям СТРОГО как в таблице
        # (Например: coach, workout, nutrition, dating, natal)
        raw_categories = defaultdict(list)
        
        for q in questions:
            # Берем название категории из первой колонки таблицы
            cat_name = q.get('category', 'common').strip().lower()
            raw_categories[cat_name].append(q)

        # 3. Сохраняем КАЖДУЮ категорию в Redis под своим именем
        # В Redis появятся ключи: survey_config:coach, survey_config:workout и т.д.
        for cat_name, q_list in raw_categories.items():
            if q_list:
                # ВАЖНО: Мы используем метод set_survey_config твоего redis_service
                await redis_service.set_survey_config(cat_name, q_list)
                log.info("category_saved", category=cat_name, count=len(q_list))

        # 4. Сохраняем системные промпты для ИИ
        if prompts:
            for mode, text in prompts.items():
                await redis_service.set_prompt(mode, text)
            log.info("prompts_saved", keys=list(prompts.keys()))
            
        log.info("sync_finished_successfully")
        return True

    except Exception as e:
        log.error("sync_failed", error=str(e))
        # Пробрасываем ошибку, чтобы scheduler мог её залогировать
        raise e

if __name__ == "__main__":
    # Код для ручного запуска из терминала
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    print("🚀 Запуск синхронизации анкет...")
    asyncio.run(update_surveys())