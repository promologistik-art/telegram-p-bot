import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
    
    # Лимиты по умолчанию
    DEFAULT_MAX_PROJECTS = int(os.getenv("DEFAULT_MAX_PROJECTS", "1"))
    DEFAULT_MAX_SOURCES_PER_PROJECT = int(os.getenv("DEFAULT_MAX_SOURCES_PER_PROJECT", "3"))
    DEFAULT_CHECK_INTERVAL = int(os.getenv("DEFAULT_CHECK_INTERVAL", "60"))
    
    # Настройки публикации
    DEFAULT_POST_INTERVAL_HOURS = int(os.getenv("DEFAULT_POST_INTERVAL_HOURS", "2"))
    MIN_POST_INTERVAL_MINUTES = int(os.getenv("MIN_POST_INTERVAL_MINUTES", "30"))
    DEFAULT_ACTIVE_HOURS_START = int(os.getenv("DEFAULT_ACTIVE_HOURS_START", "8"))
    DEFAULT_ACTIVE_HOURS_END = int(os.getenv("DEFAULT_ACTIVE_HOURS_END", "22"))
    
    # Глобальные настройки
    SHOW_SOURCE_SIGNATURE = os.getenv("SHOW_SOURCE_SIGNATURE", "false").lower() == "true"
    
    TIMEZONE = "Europe/Moscow"
    
    # Пути
    DATA_DIR = "data"
    DB_PATH = os.path.join(DATA_DIR, "bot.db")
    TEMP_DIR = "temp"
    BACKUP_DIR = "backups"
    
    SCRAPER_TIMEOUT = 30
    SCRAPER_RETRIES = 3
    SCRAPER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is required")
        if not cls.ADMIN_ID:
            raise ValueError("ADMIN_ID is required")
    
    @classmethod
    def toggle_source_signature(cls):
        cls.SHOW_SOURCE_SIGNATURE = not cls.SHOW_SOURCE_SIGNATURE
        return cls.SHOW_SOURCE_SIGNATURE

Config.validate()