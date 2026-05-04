# src/services/google_docs.py
import re
import aiohttp
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from src.config import settings
from src.utils.logger import logger
from src.utils.metrics import SYSTEM_ERRORS
from src.utils.alerting import send_alert

def get_token():
    """
    Получает access_token для доступа к Google Docs API.
    """
    creds = Credentials.from_service_account_file(
        settings.GOOGLE_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/documents.readonly"]
    )
    creds.refresh(Request())
    return creds.token

async def fetch_prompts_from_doc():
    """
    Загружает текст из Google Doc и парсит его по хештегам.
    Формат в доке: #coach_combined# Текст промпта... #coach_nutrition# Другой текст...
    """
    log = logger.bind(service="google_docs")
    doc_id = settings.GOOGLE_DOC_ID
    
    try:
        token = get_token()
        url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
        headers = {"Authorization": f"Bearer {token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Google Docs API error: {resp.status} - {text}")
                
                doc_data = await resp.json()
                
        # Извлекаем весь текст из структуры документа
        full_text = ""
        content = doc_data.get("body", {}).get("content", [])
        for element in content:
            if "paragraph" in element:
                for chunk in element["paragraph"]["elements"]:
                    if "textRun" in chunk:
                        full_text += chunk["textRun"]["content"]
        
        # Парсинг хештегов: #tag_name# текст
        # Регулярка ищет #что-то# и захватывает текст до следующего # или конца
        pattern = r"#([^#\s]+)#(.*?)(?=#|$)"
        matches = re.findall(pattern, full_text, re.DOTALL)
        
        prompts = {}
        for mode, text in matches:
            mode = mode.strip().lower()
            text = text.strip()
            if mode and text:
                prompts[mode] = text
        
        log.info("google_docs_fetch_success", prompts_found=list(prompts.keys()))
        return prompts

    except Exception as e:
        log.error("google_docs_fetch_failed", error=str(e))
        SYSTEM_ERRORS.labels(service="google_docs", error_type=type(e).__name__).inc()
        await send_alert(e, context="Google Docs Prompts Sync Failure")
        return {}
