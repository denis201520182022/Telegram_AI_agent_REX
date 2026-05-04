from typing import List, Optional
from pydantic import BaseModel, Field

class PlanItem(BaseModel):
    """
    Элемент контента (строка плана).
    Пример для еды: label="Обед", content="Курица с гречкой", details="150г/100г | К:450 Б:40", emoji="🥩"
    Пример для спорта: label="Приседания", content="3 подхода по 12 раз", details="Отдых 90 сек | Техника: спина ровно", emoji="🏋️"
    """
    label: str = Field(
        ..., 
        description="Категория (Завтрак, Обед, Упражнение, Продукт). Писать кратко, одним-двумя словами."
    )
    content: str = Field(
        ..., 
        description="Суть элемента. Если это продукт клиента, обязательно упоминать бренд 'Protein REX'."
    )
    details: Optional[str] = Field(
        None, 
        description="Технические параметры: граммовка, КБЖУ, подходы, повторы, время отдыха или краткий совет по технике."
    )
    emoji: Optional[str] = Field(
        None, 
        description="ОДИН эмодзи, максимально подходящий по смыслу (🍳, 🥩, 🍰, 🏋️, 💧, 🔋)."
    )

class PlanSection(BaseModel):
    """
    Логический блок плана.
    Может быть днем недели ('ПОНЕДЕЛЬНИК'), списком покупок или группой мышц.
    """
    title: str = Field(
        ..., 
        description="Заголовок блока КАПСОМ. Например: 'ПОНЕДЕЛЬНИК', 'СПИСОК ПОКУПОК', 'РАЗМИНКА'."
    )
    subtitle: Optional[str] = Field(
        None, 
        description="Уточнение. Например: 'Тренировка ног', 'Цель: 1800 ккал', 'Закупить на неделю'."
    )
    items: List[PlanItem] = Field(
        ..., 
        description="Список строк (блюд, упражнений или товаров), входящих в этот блок."
    )
    footer: Optional[str] = Field(
        None, 
        description="Итоговая информация по блоку. Например: 'Всего КБЖУ: 1800/100/60/150' или 'Вода: 2.5л'."
    )

class CoachResponse(BaseModel):
    """
    Финальный ответ от ИИ-коуча.
    """
    message_to_user: str = Field(
        ..., 
        description="Текст вашего сообщения. Будьте эмпатичны, поддержите пользователя, объясните логику изменений. НЕ ИСПОЛЬЗУЙТЕ свои HTML теги."
    )
    plan_update_required: bool = Field(
        ..., 
        description="Установите true ТОЛЬКО если вы: 1. Создаете план впервые. 2. Меняете что-то в текущем плане. 3. Пользователь явно попросил 'покажи мой план'."
    )
    plan_sections: Optional[List[PlanSection]] = Field(
        None, 
        description="ПОЛНЫЙ список блоков актуального плана. Если вы вносите правку — возвращайте ВЕСЬ план целиком, но с измененным элементом."
    )