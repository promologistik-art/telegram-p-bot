import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, func
from config import Config
from database import AsyncSessionLocal
from models import User, Project
from .utils import is_admin

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new_user = False
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalar_one_or_none()
        
        if not db_user:
            is_new_user = True
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                full_name=user.full_name,
                is_admin=(user.id == Config.ADMIN_ID),
                max_projects=Config.DEFAULT_MAX_PROJECTS,
                max_sources_per_project=Config.DEFAULT_MAX_SOURCES_PER_PROJECT
            )
            session.add(db_user)
            await session.commit()
            logger.info(f"New user: {user.id}")
        
        result = await session.execute(
            select(func.count()).select_from(Project).where(Project.user_id == user.id)
        )
        projects_count = result.scalar()
        has_project = projects_count > 0
    
    if is_new_user and user.id != Config.ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=Config.ADMIN_ID,
                text=(
                    f"🆕 <b>Новый пользователь!</b>\n\n"
                    f"👤 {user.full_name or '—'}\n"
                    f"📝 @{user.username or 'нет username'}\n"
                    f"🆔 <code>{user.id}</code>\n"
                    f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
    
    welcome = (
        f"👋 Привет, {user.first_name or 'пользователь'}!\n\n"
        "Я бот для автоматического парсинга и публикации постов из Telegram-каналов.\n\n"
    )
    
    if not has_project:
        welcome += (
            "🚀 Для начала работы создайте первый проект:\n"
            "/my_projects — перейти к проектам\n\n"
        )
    
    welcome += (
        "📋 Основные команды:\n"
        "/my_projects — мои проекты\n"
        "/add_source — добавить источник\n"
        "/add_target — добавить целевой канал\n"
        "/status — статистика\n"
        "/help — все команды"
    )
    
    await update.message.reply_text(welcome)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 <b>Справка по командам</b>\n\n"
        "<b>Проекты:</b>\n"
        "/my_projects - список ваших проектов\n\n"
        "<b>Источники:</b>\n"
        "/add_source - добавить канал для парсинга\n"
        "/my_sources - список источников\n\n"
        "<b>Целевые каналы:</b>\n"
        "/add_target - добавить канал для публикации\n"
        "/my_targets - список целевых каналов\n\n"
        "<b>Настройки:</b>\n"
        "/set_interval - интервал парсинга\n\n"
        "<b>Управление:</b>\n"
        "/status - общая статистика\n"
        "/project_stats - статистика по проекту\n"
        "/parse - запустить парсинг сейчас\n"
        "/queue - очередь публикации\n"
        "/postnow - опубликовать следующий пост немедленно\n"
    )
    
    if await is_admin(update.effective_user.id):
        text += "\n<b>Админ:</b> /admin"
    
    await update.message.reply_text(text, parse_mode="HTML")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено")
    return ConversationHandler.END