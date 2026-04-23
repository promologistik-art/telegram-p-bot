import logging
import re
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
    
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await update.message.reply_text(message)
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    
    min_interval = user.min_check_interval_minutes if not user.is_admin else 30
    
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
    
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await update.message.reply_text(message)
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    
    min_interval = user.min_post_interval_minutes if not user.is_admin else 30
    
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


# ============ НАСТРОЙКА ПОДПИСИ ============

def extract_username_from_link(link: str) -> str:
    """Извлекает username из ссылки t.me."""
    # t.me/username или https://t.me/username или @username
    patterns = [
        r'(?:https?://)?t\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    return None


def parse_signature_input(text: str) -> str:
    """
    Парсит ввод пользователя и возвращает HTML-подпись.
    
    Форматы:
    - "Просто текст" -> "Просто текст"
    - "Текст | https://t.me/username" -> '<a href="https://t.me/username">Текст</a>'
    - "https://t.me/username" -> '<a href="https://t.me/username">@username</a>'
    - "Сделано в https://t.me/username" -> 'Сделано в <a href="https://t.me/username">@username</a>'
    """
    text = text.strip()
    
    # Формат "Текст | ссылка"
    if "|" in text:
        parts = text.split("|", 1)
        label = parts[0].strip()
        link = parts[1].strip()
        
        if link:
            if not link.startswith("http"):
                link = "https://" + link
            
            username = extract_username_from_link(link)
            if username:
                # Если в тексте уже есть @username, не дублируем
                if f"@{username}" not in label:
                    label = f"{label} @{username}"
            
            return f'<a href="{link}">{label}</a>'
    
    # Ищем все ссылки t.me в тексте и заменяем на красивый формат
    def replace_link(match):
        full_link = match.group(0)
        username = extract_username_from_link(full_link)
        if username:
            return f'<a href="{full_link}">@{username}</a>'
        return full_link
    
    # Заменяем все найденные t.me ссылки
    link_pattern = r'(?:https?://)?t\.me/[a-zA-Z0-9_]+'
    if re.search(link_pattern, text):
        text = re.sub(link_pattern, replace_link, text)
        return text
    
    # Проверяем, является ли ввод просто username (@username)
    username_match = re.match(r'^@([a-zA-Z0-9_]+)$', text)
    if username_match:
        username = username_match.group(1)
        link = f"https://t.me/{username}"
        return f'<a href="{link}">@{username}</a>'
    
    # Проверяем, является ли это просто ссылка без текста
    username = extract_username_from_link(text)
    if username:
        # Формируем полную ссылку
        if text.startswith("http"):
            link = text
        else:
            link = f"https://t.me/{username}"
        return f'<a href="{link}">@{username}</a>'
    
    # Если ничего не подошло — возвращаем как простой текст
    return text


async def set_signature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало настройки подписи."""
    project = await require_project(update, context)
    if not project:
        return ConversationHandler.END
    
    current = project.signature or "не установлена"
    # Экранируем HTML для отображения
    current_display = current.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    context.user_data['temp_project_id'] = project.id
    
    await update.message.reply_text(
        f"✍️ <b>Подпись проекта «{project.name}»</b>\n\n"
        f"<b>Текущая подпись:</b>\n{current_display}\n\n"
        f"<b>Введите подпись:</b>\n\n"
        f"📝 <b>Просто текст:</b>\n"
        f"   <code>Мой канал</code>\n\n"
        f"🔗 <b>Текст + ссылка (через | ):</b>\n"
        f"   <code>Мой канал | https://t.me/username</code>\n\n"
        f"🔗 <b>Текст со ссылкой внутри:</b>\n"
        f"   <code>Сделано в https://t.me/username</code>\n"
        f"   <i>Бот сам заменит ссылку на красивый @username</i>\n\n"
        f"🔗 <b>Только ссылка:</b>\n"
        f"   <code>https://t.me/username</code>\n\n"
        f"Отправьте <code>удалить</code> чтобы убрать подпись.\n"
        f"/cancel — отмена",
        parse_mode="HTML"
    )
    return AWAITING_SIGNATURE


async def set_signature_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение подписи."""
    text = update.message.text.strip()
    project_id = context.user_data.get('temp_project_id')
    
    if text.lower() == "удалить":
        signature = None
        reply = "✅ Подпись удалена"
    else:
        # Парсим ввод и создаём HTML-подпись
        signature = parse_signature_input(text)
        
        # Экранируем для отображения
        display_text = signature.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        reply = (
            f"✅ <b>Подпись установлена:</b>\n\n"
            f"{display_text}\n\n"
            f"💡 Подпись будет добавляться в конце каждого поста."
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