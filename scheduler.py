import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from database import AsyncSessionLocal, is_post_parsed, mark_post_parsed
from models import User, Project, SourceChannel, TargetChannel, PostQueue
from scraper import TelegramScraper
from poster import PosterService
from utils import calculate_score, get_moscow_time, format_number
from config import Config

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, poster: PosterService):
        self.poster = poster
        self._running = False
        self._tasks = {}
        self._last_daily_report = None  # Дата последнего ежедневного отчёта

    async def start(self):
        self._running = True
        logger.info("🟢 Scheduler started")
        
        while self._running:
            try:
                await self._check_projects()
                await self._check_daily_tasks()
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    async def _check_daily_tasks(self):
        """Проверка ежедневных задач (отчёты, уведомления)."""
        now = get_moscow_time()
        
        # Ежедневный отчёт в 9:00 МСК
        if now.hour == 9 and now.minute == 0:
            today = now.date()
            if self._last_daily_report != today:
                self._last_daily_report = today
                await self._send_daily_report()
                await self._send_trial_warnings()

    async def _send_daily_report(self):
        """Отправить ежедневный отчёт админу."""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(User).order_by(User.created_at.desc()))
                users = result.scalars().all()
            
            now = datetime.utcnow()
            total_users = len(users)
            new_today = sum(1 for u in users if u.created_at and (now - u.created_at).days < 1)
            on_trial = sum(1 for u in users if not u.subscription_active and u.trial_ends_at and u.trial_ends_at > now)
            paid = sum(1 for u in users if u.subscription_active)
            
            # Пользователи с заканчивающимся триалом
            trial_ending = []
            for u in users:
                if not u.subscription_active and u.trial_ends_at:
                    days_left = (u.trial_ends_at - now).days
                    if 0 <= days_left <= 2:
                        trial_ending.append((u, days_left))
            
            # Пользователи с заканчивающейся подпиской
            sub_ending = []
            for u in users:
                if u.subscription_active and u.subscription_ends_at:
                    days_left = (u.subscription_ends_at - now).days
                    if 0 <= days_left <= 3:
                        sub_ending.append((u, days_left))
            
            report_text = (
                f"📊 <b>Ежедневный отчёт</b>\n"
                f"📅 {now.strftime('%d.%m.%Y')}\n\n"
                f"👥 Всего пользователей: {total_users}\n"
                f"🆕 Новых за сутки: {new_today}\n"
                f"🎁 На триале: {on_trial}\n"
                f"💎 Платных: {paid}\n"
            )
            
            if trial_ending:
                report_text += "\n<b>⚠️ Триал заканчивается:</b>\n"
                for u, days in trial_ending[:10]:
                    report_text += f"• @{u.username or u.telegram_id} — {days} дн.\n"
            
            if sub_ending:
                report_text += "\n<b>📅 Подписка заканчивается:</b>\n"
                for u, days in sub_ending[:10]:
                    report_text += f"• @{u.username or u.telegram_id} — {days} дн.\n"
            
            # Отправляем админу
            from telegram import Bot
            bot = Bot(token=Config.BOT_TOKEN)
            await bot.send_message(
                chat_id=Config.ADMIN_ID,
                text=report_text,
                parse_mode="HTML"
            )
            
            logger.info("Daily report sent to admin")
            
        except Exception as e:
            logger.error(f"Failed to send daily report: {e}")

    async def _send_trial_warnings(self):
        """Отправить предупреждения пользователям о скором окончании триала/подписки."""
        now = datetime.utcnow()
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
            
            from telegram import Bot
            bot = Bot(token=Config.BOT_TOKEN)
            
            for user in users:
                if user.is_admin:
                    continue
                
                # Предупреждение о триале (за 1 день)
                if not user.subscription_active and user.trial_ends_at:
                    days_left = (user.trial_ends_at - now).days
                    if days_left == 1 and (not user.last_trial_warning_sent or (now - user.last_trial_warning_sent).days >= 1):
                        try:
                            await bot.send_message(
                                chat_id=user.telegram_id,
                                text=(
                                    f"⚠️ <b>Ваш пробный период заканчивается завтра!</b>\n\n"
                                    f"📅 До: {user.trial_ends_at.strftime('%d.%m.%Y')}\n\n"
                                    f"Для продолжения работы свяжитесь с администратором.\n\n"
                                    f"💎 Тарифы:\n"
                                    f"• Базовый — 290 ₽/мес\n"
                                    f"• Стандарт — 590 ₽/мес\n"
                                    f"• PRO — 990 ₽/мес\n"
                                    f"• Безлимит — 1990 ₽/мес"
                                ),
                                parse_mode="HTML"
                            )
                            user.last_trial_warning_sent = now
                        except Exception as e:
                            logger.error(f"Failed to send trial warning to {user.telegram_id}: {e}")
                
                # Предупреждение о подписке (за 3 дня)
                if user.subscription_active and user.subscription_ends_at:
                    days_left = (user.subscription_ends_at - now).days
                    if days_left == 3 and (not user.last_subscription_warning_sent or (now - user.last_subscription_warning_sent).days >= 1):
                        try:
                            await bot.send_message(
                                chat_id=user.telegram_id,
                                text=(
                                    f"📅 <b>Ваша подписка заканчивается через 3 дня!</b>\n\n"
                                    f"📅 До: {user.subscription_ends_at.strftime('%d.%m.%Y')}\n\n"
                                    f"Для продления свяжитесь с администратором."
                                ),
                                parse_mode="HTML"
                            )
                            user.last_subscription_warning_sent = now
                        except Exception as e:
                            logger.error(f"Failed to send subscription warning to {user.telegram_id}: {e}")
            
            await session.commit()

    async def _check_projects(self):
        """Проверить все активные проекты."""
        now = datetime.utcnow()
        current_minute = now.minute
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Project).where(Project.is_active == True)
            )
            projects = result.scalars().all()
        
        logger.debug(f"Checking {len(projects)} projects at minute {current_minute}")
        
        for project in projects:
            # Получаем пользователя для проверки лимитов
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(User).where(User.telegram_id == project.user_id)
                )
                user = result.scalar_one_or_none()
                
                if not user:
                    continue
                
                # Проверяем доступ пользователя
                if not user.is_admin:
                    has_access = False
                    if user.subscription_active:
                        if user.subscription_ends_at and user.subscription_ends_at > now:
                            has_access = True
                    elif user.trial_ends_at and user.trial_ends_at > now:
                        has_access = True
                    
                    if not has_access:
                        continue  # Пропускаем проект, если нет доступа
                
                # Используем интервал пользователя или проекта
                interval = min(project.check_interval_minutes, user.min_check_interval_minutes) if not user.is_admin else project.check_interval_minutes
                slot = max(interval // 60, 1)
                
                if current_minute % slot == 0:
                    task_key = f"project_{project.id}"
                    if task_key not in self._tasks or self._tasks[task_key].done():
                        task = asyncio.create_task(self._process_project(project))
                        self._tasks[task_key] = task
                        logger.info(f"⏰ Project '{project.name}' (ID: {project.id}) scheduled")

    async def _process_project(self, project: Project):
        """Обработать один проект."""
        logger.info(f"🔍 Processing project '{project.name}' (ID: {project.id})")
        
        # Проверяем доступ пользователя
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == project.user_id)
            )
            user = result.scalar_one_or_none()
            
            if not user:
                logger.warning(f"⚠️ User for project '{project.name}' not found")
                return
            
            # Проверка доступа (кроме админа)
            if not user.is_admin:
                has_access = False
                now = datetime.utcnow()
                if user.subscription_active:
                    if user.subscription_ends_at and user.subscription_ends_at > now:
                        has_access = True
                elif user.trial_ends_at and user.trial_ends_at > now:
                    has_access = True
                
                if not has_access:
                    logger.warning(f"⚠️ User {user.telegram_id} has no access, skipping project '{project.name}'")
                    return
        
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
        
        posts_to_publish = []
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
                best_is_fallback = True
                
                for post in posts:
                    if await is_post_parsed(post["url"]):
                        continue
                    
                    post["source_username"] = source.channel_username
                    post["source_title"] = source.channel_title
                    
                    # Используем timestamp поста если есть, иначе текущее время
                    post_time = datetime.utcnow()
                    if post.get("datetime"):
                        try:
                            post_time = datetime.fromisoformat(post["datetime"].replace("Z", "+00:00"))
                        except:
                            pass
                    
                    score, is_fallback = calculate_score(post, source.criteria, post_time)
                    
                    # Приоритет: сначала посты прошедшие критерии, потом fallback
                    if not is_fallback and score > best_score:
                        best_score = score
                        best_post = post
                        best_is_fallback = False
                    elif best_is_fallback and is_fallback and score > best_score:
                        best_score = score
                        best_post = post
                
                if best_post:
                    has_content = best_post.get("text") or (best_post.get("has_media") and best_post.get("media_url"))
                    
                    if not has_content:
                        logger.warning(f"⚠️ Skipping empty post from @{source.channel_username}")
                        continue
                    
                    logger.info(f"🏆 Selected from @{source.channel_username}: score={best_score}, views={best_post.get('views')}, fallback={best_is_fallback}")
                    
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
                    
                    posts_to_publish.append(best_post)
                    
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
        
        if posts_to_publish:
            logger.info(f"📤 Found {len(posts_to_publish)} posts for project '{project.name}'")
            
            # Получаем текущее московское время (offset-naive)
            current_time = get_moscow_time().replace(tzinfo=None)
            
            # Получаем последнее запланированное время из очереди
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(PostQueue)
                    .where(PostQueue.project_id == project.id)
                    .order_by(PostQueue.scheduled_time.desc())
                    .limit(1)
                )
                last_queued = result.scalar_one_or_none()
            
            if last_queued:
                last_time_msk = last_queued.scheduled_time + timedelta(hours=3)
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
            
            # Интервал между постами (используем настройки пользователя)
            interval_minutes = max(project.post_interval_hours * 60, user.min_post_interval_minutes)
            interval_minutes = max(interval_minutes, Config.MIN_POST_INTERVAL_MINUTES)
            
            total_posted = 0
            
            for i, post in enumerate(posts_to_publish):
                if i > 0:
                    next_time = next_time + timedelta(minutes=interval_minutes)
                    
                    if next_time.hour >= project.active_hours_end:
                        next_time = (next_time + timedelta(days=1)).replace(
                            hour=project.active_hours_start, minute=0, second=0, microsecond=0
                        )
                
                utc_time = next_time - timedelta(hours=3)
                
                await self.poster.add_to_queue(
                    project_id=project.id,
                    target_channel_id=target.channel_id,
                    post_data=post,
                    scheduled_time=utc_time
                )
                total_posted += 1
                
                logger.info(f"📅 Post {i+1} from @{post.get('source_username')} scheduled for {next_time.strftime('%d.%m.%Y %H:%M')} MSK")
            
            # Обновляем статистику проекта и пользователя
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
                
                # Обновляем статистику пользователя
                result = await session.execute(
                    select(User).where(User.telegram_id == project.user_id)
                )
                db_user = result.scalar_one_or_none()
                if db_user:
                    if db_user.last_reset.date() < today:
                        db_user.posts_parsed_today = 0
                        db_user.posts_posted_today = 0
                        db_user.last_reset = datetime.utcnow()
                    
                    db_user.posts_parsed_today += total_parsed
                
                await session.commit()
                logger.info(f"📊 Stats updated: +{total_parsed} parsed, +{total_posted} queued")
        
        logger.info(f"✅ Project '{project.name}' processing completed")

    async def stop(self):
        logger.info("🛑 Stopping scheduler...")
        self._running = False
        
        for task_key, task in self._tasks.items():
            if not task.done():
                task.cancel()
                logger.info(f"❌ Cancelled task {task_key}")
        
        logger.info("🔴 Scheduler stopped")