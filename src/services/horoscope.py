from datetime import date
from prometheus_client import Counter
from src.utils.logger import logger

# --- МЕТРИКИ ---
# Позволяет отслеживать распределение аудитории по знакам зодиака в Grafana
ZODIAC_CALCULATION_TOTAL = Counter(
    'rex_zodiac_calculated_total', 
    'Total zodiac signs calculated', 
    ['sign']
)

# Справочник для логики и планировщика
RUS_SIGNS = {
    "aries": "Овен", 
    "taurus": "Телец", 
    "gemini": "Близнецы", 
    "cancer": "Рак",
    "leo": "Лев", 
    "virgo": "Дева", 
    "libra": "Весы", 
    "scorpio": "Скорпион",
    "sagittarius": "Стрелец", 
    "capricorn": "Козерог", 
    "aquarius": "Водолей", 
    "pisces": "Рыбы"
}

def get_zodiac_sign(birth_date: date) -> str:
    """
    Вычисляет знак зодиака (на латинице) по переданному объекту date.
    Используется при заполнении анкеты 'natal'.
    """
    try:
        day = birth_date.day
        month = birth_date.month
        
        # По умолчанию Рыбы (закрывают цикл)
        sign = "pisces" 
        
        # Логика определения по периодам
        if (month == 3 and day >= 21) or (month == 4 and day <= 19): 
            sign = "aries"
        elif (month == 4 and day >= 20) or (month == 5 and day <= 20): 
            sign = "taurus"
        elif (month == 5 and day >= 21) or (month == 6 and day <= 20): 
            sign = "gemini"
        elif (month == 6 and day >= 21) or (month == 7 and day <= 22): 
            sign = "cancer"
        elif (month == 7 and day >= 23) or (month == 8 and day <= 22): 
            sign = "leo"
        elif (month == 8 and day >= 23) or (month == 9 and day <= 22): 
            sign = "virgo"
        elif (month == 9 and day >= 23) or (month == 10 and day <= 22): 
            sign = "libra"
        elif (month == 10 and day >= 23) or (month == 11 and day <= 21): 
            sign = "scorpio"
        elif (month == 11 and day >= 22) or (month == 12 and day <= 21): 
            sign = "sagittarius"
        elif (month == 12 and day >= 22) or (month == 1 and day <= 19): 
            sign = "capricorn"
        elif (month == 1 and day >= 20) or (month == 2 and day <= 18): 
            sign = "aquarius"
        
        # Инкремент метрики
        ZODIAC_CALCULATION_TOTAL.labels(sign=sign).inc()
        
        return sign

    except Exception as e:
        logger.error("zodiac_calculation_failed", error=str(e), input_value=str(birth_date))
        raise e

def get_russian_sign_name(sign_en: str) -> str:
    """
    Преобразует английский ключ знака в русское название.
    Если ключ не найден, возвращает 'Зодиак'.
    """
    return RUS_SIGNS.get(sign_en.lower(), "Зодиак")