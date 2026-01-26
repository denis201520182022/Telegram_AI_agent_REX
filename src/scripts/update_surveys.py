import asyncio
import sys
from os.path import abspath, dirname

# Пути
sys.path.insert(0, dirname(dirname(dirname(abspath(__file__)))))

from src.services.sheets import fetch_all_data
from src.services.redis import redis_service
from src.utils.logger import logger

async def update_surveys():
    log = logger.bind(task="update_surveys")
    log.info("started")
    
    try:
        # 1. Получаем данные (questions - это список, prompts - словарь)
        questions, prompts = await fetch_all_data()
        
        # 2. Сохраняем вопросы для Коуча
        # В будущем можно разделить вопросы на dating/coach еще в таблице, 
        # но пока предположим, что в таблице вопросы только для коуча.
        # Либо можно добавить колонку 'mode' в таблицу, но мы использовали 'category'.
        # Давай считать, что этот список вопросов доступен под ключом 'coach'.
        
        if questions:
            await redis_service.set_survey_config("coach", questions)
            log.info("questions_saved", count=len(questions))

        # 3. Сохраняем промпты
        if prompts:
            for mode, text in prompts.items():
                await redis_service.set_prompt(mode, text)
            log.info("prompts_saved", keys=list(prompts.keys()))
            
    except Exception as e:
        log.error("failed", error=str(e))
        raise e

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(update_surveys())