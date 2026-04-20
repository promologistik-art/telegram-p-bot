import logging
from telegram import Update, BotCommand
from telegram.ext import ContextTypes
from sqlalchemy import select, func
from database import AsyncSessionLocal
from models import User, Project, SourceChannel, TargetChannel
from config import Config
from .constants import CURRENT_PROJECT_KEY

logger = logging.getLogger(__name__)


async def get_current_project(telegram_id: int, context: ContextTypes.DEFAULT_TYPE) -> Project:
    project_id = context.user_data.get(CURRENT_PROJECT_KEY)
    
    async with AsyncSessionLocal() as session:
        if project_id:
            result = await session.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.user_id == telegram_id
                )
            )
            project = result.scalar_one_or_none()
            if project:
                return project
        
        result = await session.execute(
            select(Project).where(
                Project.user_id == telegram_id,
                Project.is_active == True
            ).order_by(Project.id)
        )
        project = result.scalars().first()
        
        if project:
            context.user_data[CURRENT_PROJECT_KEY] = project.id
        
        return project


async def require_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Project:
    telegram_id = update.effective_user.id
    project = await get_current_project(telegram_id, context)
    
    if not project:
        await update.message.reply_text(
            "❌ У вас нет проектов.\n"
            "Создайте первый проект через /my_projects"
        )
        return None
    
    return project


async def is_admin(telegram_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        return user and user.is_admin


async def setup_bot_commands(application):
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("my_projects", "📁 Мои проекты"),
        BotCommand("add_source", "📥 Добавить источник"),
        BotCommand("add_target", "📤 Добавить целевой канал"),
        BotCommand("my_sources", "📊 Мои источники"),
        BotCommand("my_targets", "🎯 Мои целевые каналы"),
        BotCommand("set_interval", "⏰ Интервал парсинга"),
        BotCommand("set_signature", "✍️ Подпись под постами"),
        BotCommand("status", "📈 Статистика"),
        BotCommand("parse", "🔄 Парсинг сейчас"),
        BotCommand("queue", "📬 Очередь публикации"),
        BotCommand("postnow", "🚀 Опубликовать сейчас"),
        BotCommand("help", "📋 Помощь"),
    ]
    await application.bot.set_my_commands(commands)


async def get_sources_count(project_id: int) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(SourceChannel).where(SourceChannel.project_id == project_id)
        )
        return result.scalar()


async def get_project_target(project_id: int) -> TargetChannel:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TargetChannel).where(
                TargetChannel.project_id == project_id,
                TargetChannel.is_active == True
            )
        )
        return result.scalar_one_or_none()


async def get_user_projects_count(telegram_id: int) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(Project).where(Project.user_id == telegram_id)
        )
        return result.scalar()


async def send_project_ready_message(update: Update, project_name: str):
    text = (
        f"✅ <b>Проект «{project_name}» готов к работе!</b>\n\n"
        f"📋 Что дальше:\n"
        f"• /set_interval — настроить частоту парсинга\n"
        f"• /set_signature — установить подпись\n"
        f"• /parse — запустить первый парсинг\n"
        f"• /status — смотреть статистику\n\n"
        f"🤖 Бот начнёт автоматическую работу согласно настройкам."
    )
    await update.message.reply_text(text, parse_mode="HTML")