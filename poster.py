import asyncio
import logging
import os
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
from sqlalchemy import select, update
from database import AsyncSessionLocal
from models import PostQueue, PublishedPost, TargetChannel, Project
from utils import clean_caption

logger = logging.getLogger(__name__)


class PosterService:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._running = False
        self.rate_limit = asyncio.Semaphore(8)

    async def add_to_queue(self, project_id: int, target_channel_id: int, post_data: dict, scheduled_time: datetime):
        async with AsyncSessionLocal() as session:
            queue_item = PostQueue(
                project_id=project_id,
                target_channel_id=target_channel_id,
                post_data=post_data,
                scheduled_time=scheduled_time,
                status="pending"
            )
            session.add(queue_item)
            await session.commit()
            logger.info(f"📨 Post queued for project {project_id}, scheduled at {scheduled_time}")

    async def get_pending_posts(self) -> list:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(PostQueue).where(
                    PostQueue.status == "pending",
                    PostQueue.scheduled_time <= datetime.utcnow()
                ).order_by(PostQueue.scheduled_time)
            )
            return result.scalars().all()

    async def publish_post(self, queue_item: PostQueue) -> bool:
        async with self.rate_limit:
            try:
                post_data = queue_item.post_data
                caption = clean_caption(post_data.get("text", ""))
                
                # Получаем подпись проекта
                async with AsyncSessionLocal() as session:
                    result = await session.execute(
                        select(Project).where(Project.id == queue_item.project_id)
                    )
                    project = result.scalar_one_or_none()
                    signature = project.signature if project else None
                
                # Добавляем подпись, если она есть
                if signature:
                    if caption:
                        caption += f"\n\n{signature}"
                    else:
                        caption = signature
                
                media_path = post_data.get("media_path")
                media_type = post_data.get("media_type")
                
                if media_path and os.path.exists(media_path):
                    try:
                        with open(media_path, "rb") as f:
                            if media_type == "photo":
                                await self.bot.send_photo(
                                    chat_id=queue_item.target_channel_id,
                                    photo=f,
                                    caption=caption if caption else None
                                )
                            elif media_type == "video":
                                await self.bot.send_video(
                                    chat_id=queue_item.target_channel_id,
                                    video=f,
                                    caption=caption if caption else None
                                )
                            else:
                                await self.bot.send_document(
                                    chat_id=queue_item.target_channel_id,
                                    document=f,
                                    caption=caption if caption else None
                                )
                        
                        try:
                            os.remove(media_path)
                        except:
                            pass
                        
                        await self._mark_published(queue_item)
                        logger.info(f"✅ Published post {queue_item.id} with media")
                        return True
                        
                    except Exception as e:
                        logger.error(f"Failed to send media: {e}")
                        if caption:
                            try:
                                await self.bot.send_message(
                                    chat_id=queue_item.target_channel_id,
                                    text=caption
                                )
                                await self._mark_published(queue_item)
                                logger.info(f"✅ Published post {queue_item.id} (text only)")
                                return True
                            except:
                                pass
                        raise e
                        
                elif caption:
                    await self.bot.send_message(
                        chat_id=queue_item.target_channel_id,
                        text=caption
                    )
                    await self._mark_published(queue_item)
                    logger.info(f"✅ Published post {queue_item.id} (text only)")
                    return True
                    
                else:
                    logger.warning(f"⚠️ Empty post {queue_item.id}, marking as failed")
                    await self._mark_failed(queue_item, "Empty post")
                    return False
                    
            except TelegramError as e:
                logger.error(f"Telegram error for post {queue_item.id}: {e}")
                await self._mark_failed(queue_item, str(e)[:200])
                return False
                
            except Exception as e:
                logger.error(f"Unexpected error for post {queue_item.id}: {e}")
                await self._mark_failed(queue_item, str(e)[:200])
                return False

    async def _mark_published(self, queue_item: PostQueue):
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(PostQueue)
                .where(PostQueue.id == queue_item.id)
                .values(status="published", published_at=datetime.utcnow())
            )
            
            post_data = queue_item.post_data
            published = PublishedPost(
                project_id=queue_item.project_id,
                target_channel_id=queue_item.target_channel_id,
                source_channel_username=post_data.get("source_username", ""),
                post_url=post_data.get("url", ""),
                post_data=post_data
            )
            session.add(published)
            
            await session.execute(
                update(TargetChannel)
                .where(TargetChannel.channel_id == queue_item.target_channel_id)
                .values(last_posted=datetime.utcnow())
            )
            
            await session.commit()

    async def _mark_failed(self, queue_item: PostQueue, error_message: str):
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(PostQueue)
                .where(PostQueue.id == queue_item.id)
                .values(status="failed", error_message=error_message)
            )
            await session.commit()

    async def process_queue(self):
        pending = await self.get_pending_posts()
        
        if pending:
            logger.info(f"📤 Processing {len(pending)} pending posts")
            
            for queue_item in pending:
                await self.publish_post(queue_item)
                await asyncio.sleep(3)

    async def start(self):
        self._running = True
        logger.info("🟢 PosterService started")

    async def stop(self):
        self._running = False
        logger.info("🔴 PosterService stopped")