import asyncio
import logging
from datetime import datetime
from sqlalchemy import select, update
from database import AsyncSessionLocal
from models import Project, PostQueue
from poster import PosterService

logger = logging.getLogger(__name__)


class PostScheduler:
    """Планировщик публикации постов из очереди."""
    
    def __init__(self, poster: PosterService):
        self.poster = poster
        self._running = False

    async def start(self):
        """Запустить планировщик."""
        self._running = True
        logger.info("🟢 PostScheduler started")
        
        while self._running:
            try:
                await self._check_and_publish()
                await asyncio.sleep(30)  # Проверяем каждые 30 секунд
            except Exception as e:
                logger.error(f"PostScheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_and_publish(self):
        """Проверить очередь и опубликовать готовые посты."""
        await self.poster.process_queue()

    async def stop(self):
        """Остановить планировщик."""
        self._running = False
        logger.info("🔴 PostScheduler stopped")