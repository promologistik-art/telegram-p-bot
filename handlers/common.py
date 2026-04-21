import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, func
from config import Config
from database import AsyncSessionLocal
from models import User, Project
from .utils import is_admin, check_user_access, TARIFF_LIMITS

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
    
    # Уведомление админу о новом юзере
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
    
    # Проверяем доступ
    has_access, access_message, _ = await check_user_access(user.id)
    
    welcome = f"👋 Привет, {user.first_name or 'пользователь'}!\n\n"
    welcome += "Я бот для автоматического парсинга и публикации постов из Telegram-каналов.\n\n"
    
    # Информация о триале/подписке
    if not db_user.is_admin:
        now = datetime.utcnow()
        tariff_info = TARIFF_LIMITS.get(db_user.tariff, TARIFF_LIMITS["trial"])
        
        if db_user.subscription_active:
            if db_user.subscription_ends_at:
                days_left = (db_user.subscription_ends_at - now).days
                welcome += f"💎 <b>Тариф: {tariff_info['name']}</b>\n"
                welcome += f"📅 Действует до: {db_user.subscription_ends_at.strftime('%d.%m.%Y')} ({days_left} дн.)\n\n"
            else:
                welcome += f"💎 <b>Тариф: {tariff_info['name']}</b>\n\n"
        elif db_user.trial_ends_at and db_user.trial_ends_at > now:
            days_left = (db_user.trial_ends_at - now).days + 1
            welcome += f"🎁 <b>Пробный период: {days_left} дн.</b>\n"
            welcome += f"📅 До: {db_user.trial_ends_at.strftime('%d.%m.%Y')}\n\n"
            
            if days_left <= 2:
                welcome += "⚠️ <i>Пробный период скоро закончится!</i>\n"
                welcome += "<i>Свяжитесь с администратором для продления.</i>\n\n"
        else:
            welcome += "❌ <b>Доступ заблокирован</b>\n"
            welcome += "Свяжитесь с администратором для разблокировки.\n\n"
    
    if not has_project and has_access:
        welcome += (
            "🚀 Для начала работы создайте первый проект:\n"
            "/my_projects — перейти к проектам\n\n"
        )
    elif not has_access:
        welcome += "❌ Доступ ограничен. Свяжитесь с администратором.\n\n"
    
    welcome += (
        "📋 Основные команды:\n"
        "/my_projects — мои проекты\n"
        "/add_source — добавить источник\n"
        "/add_target — добавить целевой канал\n"
        "/status — статистика\n"
        "/help — все команды"
    )
    
    await update.message.reply_text(welcome, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка по командам."""
    user_id = update.effective_user.id
    
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
        "/set_interval - интервал парсинга\n"
        "/set_post_interval - интервал публикации\n"
        "/set_signature - подпись под постами\n\n"
        "<b>Управление:</b>\n"
        "/status - общая статистика\n"
        "/project_stats - статистика по проекту\n"
        "/parse - запустить парсинг сейчас\n"
        "/queue - очередь публикации\n"
        "/postnow - опубликовать следующий пост немедленно\n"
    )
    
    if await is_admin(update.effective_user.id):
        text += "\n<b>Админские команды:</b>\n"
        text += "/admin — админ-панель\n"
        text += "/admin_set_tariff — установить тариф пользователю\n"
        text += "/admin_extend_trial — продлить триал\n"
        text += "/broadcast — рассылка сообщений\n"
        text += "/clear_queue — очистить очередь\n"
        text += "/clear_failed — очистить failed посты\n"
    
    # Добавляем информацию о тарифах для обычных пользователей
    if not await is_admin(user_id):
        text += "\n<b>💎 Тарифы:</b>\n"
        text += "• Базовый — 290 ₽/мес (1 проект, 3 источника, постинг от 2ч)\n"
        text += "• Стандарт — 590 ₽/мес (3 проекта, 5 источников, постинг от 1ч)\n"
        text += "• PRO — 990 ₽/мес (10 проектов, 10 источников, постинг от 30мин)\n"
        text += "• Безлимит — 1990 ₽/мес\n\n"
        text += "📲 Для подключения: @admin"
    
    await update.message.reply_text(text, parse_mode="HTML")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменить текущее действие."""
    context.user_data.clear()
    await update.message.reply_text("❌ Действие отменено")
    return ConversationHandler.END