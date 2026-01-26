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
    creds = Credentials.from_service_account_file(settings.GOOGLE_CREDENTIALS_FILE)
    scoped = creds.with_scopes([
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    return scoped

agcm = AsyncioGspreadClientManager(get_creds)

async def fetch_all_data():
    """
    Скачивает вопросы (Лист 1) и промпты (Лист 2).
    Новая структура: category | key | type | text | options
    """
    log = logger.bind(service="google_sheets")
    start_time = time.time()
    
    try:
        agc = await agcm.authorize()
        sh = await agc.open_by_key(settings.GOOGLE_SHEET_ID)
        
        # --- 1. Читаем ВОПРОСЫ (Лист 1) ---
        ws_questions = await sh.get_worksheet(0)
        rows_q = await ws_questions.get_all_values()
        
        # Список всех вопросов (фильтровать будем в survey.py)
        questions = []
        
        if rows_q:
            # Пропускаем заголовок (rows_q[1:])
            for r in rows_q[1:]:
                # Проверка на пустую строку (нужно хотя бы 4 колонки: cat, key, type, text)
                if len(r) < 4 or not r[0]: 
                    continue
                
                category = r[0].strip().lower() # common, nutrition, workout
                key = r[1].strip()
                q_type = r[2].strip()
                text = r[3].strip()
                
                # Опции (если есть)
                options = []
                if len(r) > 4 and r[4]:
                    options = [x.strip() for x in r[4].split(',')]
                
                questions.append({
                    "category": category,
                    "key": key,
                    "type": q_type,
                    "text": text,
                    "options": options
                })

        # --- 2. Читаем ПРОМПТЫ (Лист 2) ---
        # Ищем лист с именем "Prompts" или берем второй по счету
        try:
            ws_prompts = await sh.worksheet("Prompts")
        except:
            try:
                ws_prompts = await sh.get_worksheet(1)
            except:
                ws_prompts = None

        prompts = {}
        if ws_prompts:
            rows_p = await ws_prompts.get_all_values()
            if rows_p:
                for r in rows_p[1:]: 
                    if len(r) < 2 or not r[0]: continue
                    mode = r[0].strip() # например 'coach', 'dating'
                    text = r[1].strip()
                    prompts[mode] = text

        duration = time.time() - start_time
        log.info(
            "sheets_fetch_success", 
            duration=duration, 
            total_questions=len(questions),
            prompts_found=list(prompts.keys())
        )
        
        # Возвращаем:
        # 1. Список вопросов (плоский список словарей)
        # 2. Словарь промптов
        return questions, prompts

    except Exception as e:
        log.error("sheets_fetch_failed", error=str(e))
        SYSTEM_ERRORS.labels(service="google_sheets", error_type=type(e).__name__).inc()
        await send_alert(e, context="Google Sheets Sync")
        # Если упало — пробрасываем ошибку, чтобы worker знал об этом
        raise e