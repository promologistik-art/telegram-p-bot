import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import update as sql_update
from database import AsyncSessionLocal
from models import Project
from .utils import require_project
from .constants import AWAITING_INTERVAL, AWAITING_SIGNATURE

logger = logging.getLogger(__name__)


async def set_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    
    keyboard = [
        [InlineKeyboardButton("🕐 30 минут", callback_data="interval_30")],
        [InlineKeyboardButton("🕑 1 час", callback_data="interval_60")],
        [InlineKeyboardButton("🕒 2 часа", callback_data="interval_120")],
        [InlineKeyboardButton("🕓 3 часа", callback_data="interval_180")],
        [InlineKeyboardButton("🕔 6 часов", callback_data="interval_360")],
        [InlineKeyboardButton("🕕 12 часов", callback_data="interval_720")],
    ]
    
    await update.message.reply_text(
        f"⏰ <b>Интервал парсинга</b>\n\n"
        f"Проект: {project.name}\n"
        f"Текущий: {project.check_interval_minutes} мин\n\n"
        f"Выберите новый интервал:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_INTERVAL


async def set_interval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    interval = int(query.data.replace("interval_", ""))
    project_id = context.user_data.get('temp_project_id')
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(Project)
            .where(Project.id == project_id)
            .values(check_interval_minutes=interval)
        )
        await session.commit()
    
    await query.edit_message_text(f"✅ Интервал парсинга: {interval} минут")
    context.user_data.pop('temp_project_id', None)
    return ConversationHandler.END


async def set_signature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    if not project:
        return ConversationHandler.END
    
    current = project.signature or "не установлена"
    await update.message.reply_text(
        f"✍️ <b>Подпись проекта «{project.name}»</b>\n\n"
        f"Текущая: {current}\n\n"
        f"Отправьте текст подписи (или /cancel для отмены):\n\n"
        f"💡 Подпись будет добавляться в конце каждого поста.\n"
        f"Отправьте <code>удалить</code> чтобы убрать подпись.",
        parse_mode="HTML"
    )
    context.user_data['temp_project_id'] = project.id
    return AWAITING_SIGNATURE


async def set_signature_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    project_id = context.user_data.get('temp_project_id')
    
    if text.lower() == "удалить":
        signature = None
        reply = "✅ Подпись удалена"
    else:
        signature = text[:200]
        reply = f"✅ Подпись установлена:\n\n{signature}"
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(Project)
            .where(Project.id == project_id)
            .values(signature=signature)
        )
        await session.commit()
    
    await update.message.reply_text(reply, parse_mode="HTML")
    context.user_data.pop('temp_project_id', None)
    return ConversationHandler.END