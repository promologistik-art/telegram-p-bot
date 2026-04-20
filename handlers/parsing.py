import logging
from datetime import timedelta
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from database import AsyncSessionLocal
from models import Project, PostQueue
from utils import format_number
from .utils import require_project, get_sources_count, get_project_target, is_admin

logger = logging.getLogger(__name__)


async def parse_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    
    if not project:
        return
    
    target = await get_project_target(project.id)
    if not target:
        await update.message.reply_text("❌ Сначала добавьте целевой канал: /add_target")
        return
    
    sources_count = await get_sources_count(project.id)
    if sources_count == 0:
        await update.message.reply_text("❌ Сначала добавьте источники: /add_source")
        return
    
    msg = await update.message.reply_text(f"🔄 Парсинг «{project.name}»...")
    
    scheduler = context.application.bot_data.get('scheduler')
    if scheduler:
        # Сохраняем старую статистику
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project).where(Project.id == project.id))
            old_project = result.scalar_one()
            old_parsed = old_project.posts_parsed_today
        
        # Запускаем парсинг
        await scheduler._process_project(project)
        
        # Получаем обновлённую статистику
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Project).where(Project.id == project.id))
            updated = result.scalar_one()
            new_parsed = updated.posts_parsed_today - old_parsed
        
        if new_parsed > 0:
            await msg.edit_text(
                f"✅ Парсинг завершён!\n\n"
                f"📊 Найдено новых постов: {new_parsed}\n"
                f"📊 Всего спарсено сегодня: {updated.posts_parsed_today}\n"
                f"📤 В очереди: /queue"
            )
        else:
            await msg.edit_text(
                f"✅ Парсинг завершён!\n\n"
                f"📊 Новых постов не найдено\n"
                f"📊 Всего спарсено сегодня: {updated.posts_parsed_today}\n\n"
                f"💡 Возможные причины:\n"
                f"• Все посты уже были спарсены\n"
                f"• Посты не прошли критерии\n"
                f"• В каналах нет новых постов\n\n"
                f"/queue — проверить очередь"
            )
    else:
        await msg.edit_text("❌ Планировщик не найден")


async def queue_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    
    if not project:
        return
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PostQueue).where(
                PostQueue.project_id == project.id
            ).order_by(PostQueue.scheduled_time).limit(15)
        )
        items = result.scalars().all()
    
    if not items:
        await update.message.reply_text("📭 Очередь публикации пуста")
        return
    
    text = f"📬 <b>Очередь публикации «{project.name}»</b>\n\n"
    text += f"⏰ Интервал: каждые {project.post_interval_hours} ч (мин. 30 мин)\n\n"
    
    MSK_OFFSET = timedelta(hours=3)
    
    for item in items:
        post_data = item.post_data
        status_icon = {"pending": "⏳", "published": "✅", "failed": "❌"}.get(item.status, "❓")
        
        scheduled_msk = item.scheduled_time + MSK_OFFSET
        
        text += f"{status_icon} {scheduled_msk.strftime('%d.%m.%Y %H:%M')} МСК\n"
        text += f"   📡 @{post_data.get('source_username', '?')}\n"
        text += f"   👁 {format_number(post_data.get('views', 0))} | ❤️ {format_number(post_data.get('reactions', 0))}\n\n"
    
    await update.message.reply_text(text, parse_mode="HTML")


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Опубликовать следующий пост из очереди немедленно."""
    project = await require_project(update, context)
    
    if not project:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Только админ может принудительно публиковать посты")
        return
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PostQueue).where(
                PostQueue.project_id == project.id,
                PostQueue.status == "pending"
            ).order_by(PostQueue.scheduled_time).limit(1)
        )
        queue_item = result.scalar_one_or_none()
        
        if not queue_item:
            await update.message.reply_text("📭 Нет постов в очереди для публикации")
            return
        
        poster = context.application.bot_data.get('poster')
        if not poster:
            await update.message.reply_text("❌ Сервис публикации не найден")
            return
        
        msg = await update.message.reply_text("🚀 Публикую пост...")
        
        success = await poster.publish_post(queue_item)
        
        if success:
            result = await session.execute(select(Project).where(Project.id == project.id))
            db_project = result.scalar_one()
            db_project.posts_posted_today += 1
            await session.commit()
            
            post_data = queue_item.post_data
            await msg.edit_text(
                f"✅ Пост опубликован!\n\n"
                f"📡 @{post_data.get('source_username', '?')}\n"
                f"👁 {format_number(post_data.get('views', 0))} | ❤️ {format_number(post_data.get('reactions', 0))}"
            )
        else:
            await msg.edit_text(f"❌ Ошибка публикации: {queue_item.error_message or 'неизвестная ошибка'}")


async def clear_old_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить все pending посты из очереди (админ)."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа")
        return
    
    msg = await update.message.reply_text("🧹 Очищаю очередь...")
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PostQueue).where(PostQueue.status == "pending")
        )
        items = result.scalars().all()
        
        deleted = len(items)
        for item in items:
            await session.delete(item)
        
        await session.commit()
    
    await msg.edit_text(f"✅ Удалено {deleted} постов из очереди")


async def clear_failed_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить все failed посты из очереди (админ)."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа")
        return
    
    msg = await update.message.reply_text("🧹 Очищаю failed посты...")
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PostQueue).where(PostQueue.status == "failed")
        )
        items = result.scalars().all()
        
        deleted = len(items)
        for item in items:
            await session.delete(item)
        
        await session.commit()
    
    await msg.edit_text(f"✅ Удалено {deleted} failed постов")