import logging
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User, Project, PostQueue
from .utils import require_project, get_sources_count, get_project_target

logger = logging.getLogger(__name__)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one()
        
        result = await session.execute(select(Project).where(Project.user_id == telegram_id))
        projects = result.scalars().all()
        
        total_parsed = sum(p.posts_parsed_today for p in projects)
        total_posted = sum(p.posts_posted_today for p in projects)
    
    text = (
        f"📊 <b>Общая статистика</b>\n\n"
        f"📁 Проектов: {len(projects)} / {user.max_projects}\n"
        f"📈 За сегодня:\n"
        f"• Спарсено: {total_parsed}\n"
        f"• Опубликовано: {total_posted}\n\n"
        f"/my_projects — статистика по проектам"
    )
    
    await update.message.reply_text(text, parse_mode="HTML")


async def project_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    
    if not project:
        return
    
    sources_count = await get_sources_count(project.id)
    target = await get_project_target(project.id)
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PostQueue).where(PostQueue.project_id == project.id, PostQueue.status == "pending")
        )
        pending = len(result.scalars().all())
    
    text = (
        f"📊 <b>Статистика «{project.name}»</b>\n\n"
        f"📥 Источников: {sources_count}\n"
        f"📤 Цель: {target.channel_title if target else 'не задан'}\n"
        f"⏰ Интервал: {project.check_interval_minutes} мин\n"
        f"📈 Сегодня: {project.posts_parsed_today} / {project.posts_posted_today}\n"
        f"📬 В очереди: {pending}"
    )
    
    await update.message.reply_text(text, parse_mode="HTML")