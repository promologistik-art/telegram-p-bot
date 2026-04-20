import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from database import AsyncSessionLocal, is_post_parsed, mark_post_parsed
from models import User, Project, SourceChannel, TargetChannel
from scraper import TelegramScraper
from poster import PosterService
from utils import calculate_score, calculate_next_post_time, get_moscow_time
from config import Config

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, poster: PosterService):
        self.poster = poster
        self._running = False
        self._tasks = {}

    async def start(self):
        self._running = True
        logger.info("🟢 Scheduler started")
        
        while self._running:
            try:
                await self._check_projects()
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_projects(self):
        now = datetime.utcnow()
        current_minute = now.minute
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Project).where(Project.is_active == True)
            )
            projects = result.scalars().all()
        
        logger.debug(f"Checking {len(projects)} projects at minute {current_minute}")
        
        for project in projects:
            interval = project.check_interval_minutes
            slot = max(interval // 60, 1)
            
            if current_minute % slot == 0:
                task_key = f"project_{project.id}"
                if task_key not in self._tasks or self._tasks[task_key].done():
                    task = asyncio.create_task(self._process_project(project))
                    self._tasks[task_key] = task
                    logger.info(f"⏰ Project '{project.name}' (ID: {project.id}) scheduled")

    async def _process_project(self, project: Project):
        logger.info(f"🔍 Processing project '{project.name}' (ID: {project.id})")
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SourceChannel).where(
                    SourceChannel.project_id == project.id,
                    SourceChannel.is_active == True
                )
            )
            sources = result.scalars().all()
            
            result = await session.execute(
                select(TargetChannel).where(
                    TargetChannel.project_id == project.id,
                    TargetChannel.is_active == True
                )
            )
            target = result.scalar_one_or_none()
        
        if not sources:
            logger.warning(f"⚠️ Project '{project.name}' has no sources")
            return
        
        if not target:
            logger.warning(f"⚠️ Project '{project.name}' has no target channel")
            return
        
        logger.info(f"📊 Project '{project.name}': {len(sources)} sources → {target.channel_title}")
        
        posts_found = []
        total_parsed = 0
        
        async with TelegramScraper() as scraper:
            for source in sources:
                logger.info(f"📡 Fetching @{source.channel_username} for project '{project.name}'")
                
                try:
                    posts = await scraper.get_posts(source.channel_username, limit=100)
                    logger.info(f"📨 @{source.channel_username}: {len(posts)} posts fetched")
                except Exception as e:
                    logger.error(f"❌ Failed to fetch @{source.channel_username}: {e}")
                    continue
                
                best_post = None
                best_score = -1
                
                for post in posts:
                    if await is_post_parsed(post["url"]):
                        continue
                    
                    post["source_username"] = source.channel_username
                    post["source_title"] = source.channel_title
                    
                    score = calculate_score(post, source.criteria)
                    
                    if score >= 0 and score > best_score:
                        best_score = score
                        best_post = post
                
                if best_post:
                    has_content = best_post.get("text") or (best_post.get("has_media") and best_post.get("media_url"))
                    
                    if not has_content:
                        logger.warning(f"⚠️ Skipping empty post from @{source.channel_username}")
                        continue
                    
                    logger.info(f"🏆 Selected from @{source.channel_username}: score={best_score}, views={best_post.get('views')}")
                    
                    await mark_post_parsed(source.id, best_post["url"])
                    total_parsed += 1
                    
                    if best_post.get("has_media") and best_post.get("media_url"):
                        ext = "jpg" if best_post.get("media_type") == "photo" else "mp4"
                        filename = f"{uuid.uuid4()}.{ext}"
                        media_path = os.path.join(Config.TEMP_DIR, filename)
                        
                        if await scraper.download_media(best_post["media_url"], media_path):
                            best_post["media_path"] = media_path
                            logger.info(f"📎 Media downloaded for @{source.channel_username}")
                        else:
                            best_post["has_media"] = False
                            best_post["media_path"] = None
                    
                    posts_found.append(best_post)
                    
                    async with AsyncSessionLocal() as session:
                        await session.execute(
                            update(SourceChannel)
                            .where(SourceChannel.id == source.id)
                            .values(
                                last_parsed=datetime.utcnow(),
                                last_post_url=best_post["url"]
                            )
                        )
                        await session.commit()
                else:
                    logger.info(f"😴 @{source.channel_username}: no suitable posts")
        
        if posts_found:
            logger.info(f"📤 Found {len(posts_found)} posts for project '{project.name}'")
            
            # Получаем текущее московское время (offset-naive)
            current_time = get_moscow_time().replace(tzinfo=None)
            
            # Получаем последнее запланированное время из очереди
            async with AsyncSessionLocal() as session:
                from models import PostQueue
                result = await session.execute(
                    select(PostQueue)
                    .where(PostQueue.project_id == project.id)
                    .order_by(PostQueue.scheduled_time.desc())
                    .limit(1)
                )
                last_queued = result.scalar_one_or_none()
            
            if last_queued:
                # Конвертируем UTC в MSK (offset-naive)
                last_time_msk = last_queued.scheduled_time + timedelta(hours=3)
                # Оба времени offset-naive, можно сравнивать
                if last_time_msk > current_time:
                    next_time = last_time_msk
                else:
                    next_time = current_time
            else:
                next_time = current_time
            
            # Проверяем активные часы
            if next_time.hour < project.active_hours_start:
                next_time = next_time.replace(hour=project.active_hours_start, minute=0, second=0, microsecond=0)
            elif next_time.hour >= project.active_hours_end:
                next_time = (next_time + timedelta(days=1)).replace(
                    hour=project.active_hours_start, minute=0, second=0, microsecond=0
                )
            
            # Интервал между постами (минимум 30 минут)
            interval_minutes = max(project.post_interval_hours * 60, Config.MIN_POST_INTERVAL_MINUTES)
            
            scheduled_times = []
            total_posted = 0
            
            for i, post in enumerate(posts_found):
                if i > 0:
                    next_time = next_time + timedelta(minutes=interval_minutes)
                    
                    # Проверяем, не вышли ли за активные часы
                    if next_time.hour >= project.active_hours_end:
                        next_time = (next_time + timedelta(days=1)).replace(
                            hour=project.active_hours_start, minute=0, second=0, microsecond=0
                        )
                
                scheduled_times.append(next_time)
                
                # Конвертируем MSK в UTC для хранения
                utc_time = next_time - timedelta(hours=3)
                
                await self.poster.add_to_queue(
                    project_id=project.id,
                    target_channel_id=target.channel_id,
                    post_data=post,
                    scheduled_time=utc_time
                )
                total_posted += 1
                
                logger.info(f"📅 Post {i+1} scheduled for {next_time.strftime('%d.%m.%Y %H:%M')} MSK")
            
            # Обновляем статистику проекта
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Project).where(Project.id == project.id)
                )
                db_project = result.scalar_one()
                
                today = datetime.utcnow().date()
                if db_project.last_reset.date() < today:
                    db_project.posts_parsed_today = 0
                    db_project.posts_posted_today = 0
                    db_project.last_reset = datetime.utcnow()
                
                db_project.posts_parsed_today += total_parsed
                
                await session.commit()
                logger.info(f"📊 Project '{project.name}' stats updated: +{total_parsed} parsed, +{total_posted} queued")
        
        logger.info(f"✅ Project '{project.name}' processing completed")

    async def stop(self):
        logger.info("🛑 Stopping scheduler...")
        self._running = False
        
        for task_key, task in self._tasks.items():
            if not task.done():
                task.cancel()
                logger.info(f"❌ Cancelled task {task_key}")
        
        logger.info("🔴 Scheduler stopped")