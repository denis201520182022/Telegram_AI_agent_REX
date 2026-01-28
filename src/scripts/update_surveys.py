import asyncio
import sys
from collections import defaultdict
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
        # 1. Получаем данные из Google Sheets
        questions, prompts = await fetch_all_data()
        
        # 2. Группируем вопросы по режимам
        # В таблице у нас есть колонка 'category' (common, nutrition, workout, dating, natal)
        # Нам нужно сохранить их в Redis под соответствующими ключами.
        
        # Коуч использует: common + nutrition + workout
        # Дейтинг использует: common + dating
        # Натальная карта использует: common + natal
        
        grouped_questions = defaultdict(list)
        
        # Сначала соберем "сырые" категории
        raw_categories = defaultdict(list)
        for q in questions:
            cat = q.get('category', 'common')
            raw_categories[cat].append(q)
            
        # Теперь формируем готовые конфиги для режимов бота
        # --- COACH (Коуч) ---
        # Включает общие вопросы, питание и спорт.
        # Фильтрация (что конкретно задавать) происходит уже внутри бота в зависимости от выбора 🥗/🏋️
        grouped_questions['coach'] = (
            raw_categories['common'] + 
            raw_categories['nutrition'] + 
            raw_categories['workout']
        )
        
        # --- DATING (Знакомства) ---
        grouped_questions['dating'] = (
            raw_categories['common'] + 
            raw_categories['dating']
        )
        
        # --- NATAL (Натальная карта) ---
        grouped_questions['natal'] = (
            raw_categories['common'] + 
            raw_categories['natal']
        )

        # 3. Сохраняем в Redis
        for mode, q_list in grouped_questions.items():
            if q_list:
                await redis_service.set_survey_config(mode, q_list)
                log.info("config_saved", mode=mode, questions_count=len(q_list))

        # 4. Сохраняем промпты
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