from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    # --- Telegram ---
    BOT_TOKEN: SecretStr
    ADMIN_IDS: str

    # --- Database & Infra ---
    DB_URL: str
    REDIS_URL: str
    RABBIT_URL: str

    # --- Google ---
    GOOGLE_CREDENTIALS_FILE: str
    GOOGLE_SHEET_ID: str
    GOOGLE_DOC_ID: str

    # --- LLM ---
    OPENAI_API_KEY: SecretStr
    # Делаем Qwen необязательным (Optional), чтобы не падало, если его нет
    QWEN_API_KEY: Optional[SecretStr] = None 
    
    # --- Proxy ---
    SQUID_PROXY_HOST: str
    SQUID_PROXY_PORT: str
    SQUID_PROXY_USER: str
    SQUID_PROXY_PASSWORD: str

    # --- Конфигурация Pydantic ---
    model_config = SettingsConfigDict(
        env_file='.env', 
        env_file_encoding='utf-8',
        # ВАЖНО: 'ignore' говорит Pydantic'у игнорировать лишние переменные от Докера
        extra='ignore' 
    )

settings = Settings()