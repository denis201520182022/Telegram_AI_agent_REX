#src\services\sheets.py
import json
import time
from gspread_asyncio import AsyncioGspreadClientManager
from google.oauth2.service_account import Credentials
from src.config import settings

# --- OBSERVABILITY ---
from src.utils.logger import logger
from src.utils.alerting import send_alert
from src.utils.metrics import SYSTEM_ERRORS

def get_creds():
    """
    Авторизация в Google API через сервисный аккаунт.
    """
    creds = Credentials.from_service_account_file(settings.GOOGLE_CREDENTIALS_FILE)
    scoped = creds.with_scopes([
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    return scoped

# Менеджер для асинхронной работы с Google Таблицами
agcm = AsyncioGspreadClientManager(get_creds)

async def fetch_all_data():
    """
    Синхронизация данных из Google Таблиц.
    Лист 1: Вопросы анкет (Коуч, Дейтинг, Натальная карта).
    """
    log = logger.bind(service="google_sheets")
    start_time = time.time()
    
    try:
        agc = await agcm.authorize()
        sh = await agc.open_by_key(settings.GOOGLE_SHEET_ID)
        
        # --- 1. ПАРСИНГ ВОПРОСОВ (Лист 1) ---
        ws_questions = await sh.get_worksheet(0)
        rows_q = await ws_questions.get_all_values()
        
        questions = []
        
        if rows_q:
            # Пропускаем заголовок (первая строка)
            for r in rows_q[1:]:
                # Валидация: категория, ключ, тип и текст вопроса обязательны
                if len(r) < 4 or not r[0] or not r[1]: 
                    continue
                
                category = r[0].strip().lower() # common, nutrition, workout, dating, natal
                key = r[1].strip()
                q_type = r[2].strip().lower()   # text, int, button
                text = r[3].strip()
                
                # Опции для кнопок (разделяются запятой)
                options = []
                if len(r) > 4 and r[4]:
                    options = [x.strip() for x in r[4].split(',') if x.strip()]
                
                questions.append({
                    "category": category,
                    "key": key,
                    "type": q_type,
                    "text": text,
                    "options": options
                })

        duration = time.time() - start_time
        log.info(
            "sheets_fetch_success", 
            duration=round(duration, 2), 
            total_questions=len(questions)
        )
        
        return questions

    except Exception as e:
        log.error("sheets_fetch_failed", error=str(e))
        SYSTEM_ERRORS.labels(service="google_sheets", error_type=type(e).__name__).inc()
        await send_alert(e, context="Google Sheets Sync Critical Failure")
        raise e