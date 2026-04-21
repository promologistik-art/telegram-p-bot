import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import update as sql_update
from database import AsyncSessionLocal
from models import Project
from .utils import require_project, check_action_limit, check_user_access
from .constants import AWAITING_INTERVAL, AWAITING_SIGNATURE, AWAITING_POST_INTERVAL

logger = logging.getLogger(__name__)


async def set_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка интервала парсинга."""
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    # Проверяем доступ
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await update.message.reply_text(message)
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    
    # Минимальный интервал из тарифа
    min_interval = user.min_check_interval_minutes if not user.is_admin else 30
    
    # Формируем кнопки с учётом лимитов
    all_intervals = [30, 60, 120, 180, 360, 720]
    keyboard = []
    row = []
    for interval in all_intervals:
        if interval >= min_interval or user.is_admin:
            if interval < 60:
                text = f"🕐 {interval} минут"
            else:
                hours = interval // 60
                text = f"🕑 {hours} час"
                if hours in [2, 3, 4]:
                    text += "а"
                elif hours > 4:
                    text += "ов"
            row.append(InlineKeyboardButton(text, callback_data=f"interval_{interval}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)
    
    await update.message.reply_text(
        f"⏰ <b>Интервал парсинга</b>\n\n"
        f"Проект: {project.name}\n"
        f"Текущий: {project.check_interval_minutes} мин\n"
        f"Минимальный для вашего тарифа: {min_interval} мин\n\n"
        f"Выберите новый интервал:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_INTERVAL


async def set_interval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение интервала парсинга."""
    query = update.callback_query
    await query.answer()
    
    interval = int(query.data.replace("interval_", ""))
    project_id = context.user_data.get('temp_project_id')
    
    # Проверяем лимит
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await query.edit_message_text(message)
        return ConversationHandler.END
    
    can_set, limit_msg = await check_action_limit(user, "set_check_interval", interval_minutes=interval)
    if not can_set and not user.is_admin:
        await query.edit_message_text(f"❌ {limit_msg}")
        return ConversationHandler.END
    
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


async def set_post_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка интервала между публикациями."""
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    # Проверяем доступ
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await update.message.reply_text(message)
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    
    # Минимальный интервал из тарифа
    min_interval = user.min_post_interval_minutes if not user.is_admin else 30
    
    # Формируем кнопки
    all_intervals = [30, 60, 120, 180, 360, 720]
    keyboard = []
    row = []
    for interval in all_intervals:
        if interval >= min_interval or user.is_admin:
            if interval < 60:
                text = f"🕐 {interval} минут"
            else:
                hours = interval // 60
                text = f"🕑 {hours} час"
                if hours in [2, 3, 4]:
                    text += "а"
                elif hours > 4:
                    text += "ов"
            row.append(InlineKeyboardButton(text, callback_data=f"post_{interval}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)
    
    current_hours = project.post_interval_hours
    current_minutes = int(current_hours * 60)
    current_text = f"{current_minutes} минут" if current_minutes < 60 else f"{int(current_hours)} час(ов)"
    
    await update.message.reply_text(
        f"📅 <b>Интервал между публикациями</b>\n\n"
        f"Проект: {project.name}\n"
        f"Текущий: {current_text}\n"
        f"Минимальный для вашего тарифа: {min_interval} мин\n\n"
        f"Выберите новый интервал:\n"
        f"💡 Посты будут выходить с указанным интервалом в активные часы.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_POST_INTERVAL


async def set_post_interval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение интервала между публикациями."""
    query = update.callback_query
    await query.answer()
    
    minutes = int(query.data.replace("post_", ""))
    hours = minutes / 60
    project_id = context.user_data.get('temp_project_id')
    
    # Проверяем лимит
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await query.edit_message_text(message)
        return ConversationHandler.END
    
    can_set, limit_msg = await check_action_limit(user, "set_post_interval", interval_minutes=minutes)
    if not can_set and not user.is_admin:
        await query.edit_message_text(f"❌ {limit_msg}")
        return ConversationHandler.END
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(Project)
            .where(Project.id == project_id)
            .values(post_interval_hours=hours)
        )
        await session.commit()
    
    time_text = f"{minutes} минут" if minutes < 60 else f"{int(hours)} час(ов)"
    await query.edit_message_text(
        f"✅ Интервал публикации: {time_text}\n\n"
        f"💡 Посты будут выходить каждые {time_text} в активные часы."
    )
    context.user_data.pop('temp_project_id', None)
    return ConversationHandler.END


async def set_signature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройка подписи проекта."""
    project = await require_project(update, context)
    if not project:
        return ConversationHandler.END
    
    current = project.signature or "не установлена"
    current_display = current.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    await update.message.reply_text(
        f"✍️ <b>Подпись проекта «{project.name}»</b>\n\n"
        f"<b>Текущая подпись:</b>\n{current_display}\n\n"
        f"Отправьте текст подписи (или /cancel для отмены):\n\n"
        f"💡 <b>Подпись будет добавляться в конце каждого поста.</b>\n"
        f"Отправьте <code>удалить</code> чтобы убрать подпись.\n\n"
        f"🔗 <b>Кликабельные ссылки (Markdown):</b>\n"
        f"• <code>[Текст](https://t.me/username)</code> — ссылка на канал\n"
        f"• <code>@username</code> — упоминание\n"
        f"• <code>[Текст](https://site.com)</code> — ссылка на сайт\n"
        f"• <b>Жирный</b> — <code>*жирный*</code>\n"
        f"• <i>Курсив</i> — <code>_курсив_</code>\n"
        f"• <code>моноширинный</code> — <code>`моноширинный`</code>\n\n"
        f"📝 <b>Пример:</b>\n"
        f"<code>👉 [📢 Подпишись](https://t.me/my_channel) | *Важно!*</code>",
        parse_mode="HTML"
    )
    context.user_data['temp_project_id'] = project.id
    return AWAITING_SIGNATURE


async def set_signature_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение подписи."""
    text = update.message.text.strip()
    project_id = context.user_data.get('temp_project_id')
    
    if text.lower() == "удалить":
        signature = None
        reply = "✅ Подпись удалена"
    else:
        signature = text[:500]
        display_text = signature.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        reply = (
            f"✅ <b>Подпись установлена:</b>\n\n"
            f"{display_text}\n\n"
            f"💡 Подпись будет добавляться в конце каждого поста с поддержкой Markdown."
        )
    
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