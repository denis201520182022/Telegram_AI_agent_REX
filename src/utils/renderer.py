from typing import List
from src.services.schemas import PlanSection

def render_plan_to_html(sections: List[PlanSection]) -> str:
    """
    Превращает структурированные данные плана в красивый текстовый блок.
    
    ВАЖНО: Функция возвращает "сырой" подготовленный текст. 
    Оборачивание в <blockquote expandable> и дробление на части 
    происходит на стороне ai_worker.py.
    """
    if not sections:
        return ""

    html_blocks = []

    for section in sections:
        # 1. Заголовок секции (ПОНЕДЕЛЬНИК, СПИСОК ПОКУПОК и т.д.)
        # Используем жирный капс для акцента
        block_str = f"🗓 <b>{section.title.upper()}</b>"
        
        # 2. Подзаголовок (если есть), например: "Тренировка ног"
        if section.subtitle:
            block_str += f"\n<i>{section.subtitle}</i>"
        
        block_str += "\n"

        # 3. Список элементов (блюда или упражнения)
        for item in section.items:
            em = item.emoji if item.emoji else "🔹"
            
            # Формат: Эмодзи Категория: Контент (моноширинный код)
            # <code> делает текст на сером фоне в Telegram Desktop и моноширинным на мобилках
            block_str += f"\n{em} <b>{item.label}:</b> <code>{item.content}</code>"
            
            # 4. Детали (КБЖУ, Техника, Вес) через уголок └
            if item.details:
                block_str += f"\n└ <i>{item.details}</i>"
        
        # 5. Итог по блоку (футер)
        if section.footer:
            block_str += f"\n\n📊 <b>ИТОГО:</b> <code>{section.footer}</code>"
        
        html_blocks.append(block_str)

    # Соединяем блоки двойным переносом строки для визуального разделения
    return "\n\n".join(html_blocks)