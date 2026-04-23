import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import update as sql_update, select
from database import AsyncSessionLocal
from models import Project
from .utils import require_project, check_action_limit, check_user_access
from .constants import AWAITING_INTERVAL, AWAITING_SIGNATURE, AWAITING_POST_INTERVAL

logger = logging.getLogger(__name__)

AWAITING_POST_START_TIME = 17


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


# ============ НАСТРОЙКА ИНТЕРВАЛА ПУБЛИКАЦИИ ============

async def set_post_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: Выбор интервала между публикациями."""
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    telegram_id = update.effective_user.id
    has_access, message, user = await check_user_access(telegram_id)
    if not has_access:
        await update.message.reply_text(message)
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    
    min_interval = user.min_post_interval_minutes if not user.is_admin else 15
    
    all_intervals = [15, 30, 60]
    keyboard = []
    for interval in all_intervals:
        if interval >= min_interval or user.is_admin:
            text = f"🕐 {interval} минут"
            keyboard.append([InlineKeyboardButton(text, callback_data=f"post_{interval}")])
    
    current_minutes = int(project.post_interval_hours * 60)
    current_text = f"{current_minutes} минут"
    
    await update.message.reply_text(
        f"📅 <b>Интервал между публикациями</b>\n\n"
        f"Проект: {project.name}\n"
        f"Текущий интервал: {current_text}\n"
        f"Минимальный для вашего тарифа: {min_interval} мин\n\n"
        f"<b>Шаг 1 из 2:</b> Выберите интервал:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_POST_INTERVAL


async def set_post_interval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: Выбор времени первой публикации."""
    query = update.callback_query
    await query.answer()
    
    minutes = int(query.data.replace("post_", ""))
    context.user_data['temp_post_interval'] = minutes
    
    project_id = context.user_data.get('temp_project_id')
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one()
    
    # Кнопки времени с 6:00 до 23:30 с шагом 30 минут
    keyboard = []
    row = []
    for hour in range(6, 24):
        for minute in [0, 30]:
            if hour == 23 and minute == 30:
                break  # 23:30 — последний слот
            time_str = f"{hour:02d}:{minute:02d}"
            callback_data = f"starttime_{hour}_{minute}"
            row.append(InlineKeyboardButton(time_str, callback_data=callback_data))
            if len(row) == 4:
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)
    
    # Кнопки внизу
    keyboard.append([InlineKeyboardButton("🌙 Круглосуточно", callback_data="starttime_24_7")])
    keyboard.append([InlineKeyboardButton("↩️ Оставить текущее", callback_data="starttime_skip")])
    
    await query.edit_message_text(
        f"📅 <b>Интервал между публикациями</b>\n\n"
        f"Выбран интервал: <b>{minutes} минут</b>\n\n"
        f"<b>Шаг 2 из 2:</b> Выберите время первой публикации\n"
        f"или нажмите «Круглосуточно»:\n\n"
        f"💡 Посты будут выходить с выбранного времени\n"
        f"каждые {minutes} минут.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AWAITING_POST_START_TIME


async def set_post_start_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение интервала и времени старта."""
    query = update.callback_query
    await query.answer()
    
    minutes = context.user_data.get('temp_post_interval', 30)
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
    
    # Если пропустил выбор времени
    if query.data == "starttime_skip":
        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_update(Project)
                .where(Project.id == project_id)
                .values(post_interval_hours=hours)
            )
            await session.commit()
        
        await query.edit_message_text(
            f"✅ Интервал обновлён: <b>{minutes} минут</b>\n"
            f"🕐 Время старта без изменений.",
            parse_mode="HTML"
        )
        
        context.user_data.pop('temp_project_id', None)
        context.user_data.pop('temp_post_interval', None)
        return ConversationHandler.END
    
    # Если выбрано "Круглосуточно"
    if query.data == "starttime_24_7":
        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_update(Project)
                .where(Project.id == project_id)
                .values(
                    post_interval_hours=hours,
                    active_hours_start=0,
                    active_hours_end=24
                )
            )
            await session.commit()
        
        await query.edit_message_text(
            f"✅ Интервал: <b>{minutes} минут</b>\n"
            f"🌙 Режим: <b>круглосуточно</b>\n\n"
            f"💡 Бот будет публиковать посты 24/7 каждые {minutes} минут.",
            parse_mode="HTML"
        )
        
        context.user_data.pop('temp_project_id', None)
        context.user_data.pop('temp_post_interval', None)
        return ConversationHandler.END
    
    # Парсим время из callback_data (starttime_H_M)
    parts = query.data.split("_")
    hour = int(parts[1])
    minute = int(parts[2])
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_update(Project)
            .where(Project.id == project_id)
            .values(
                post_interval_hours=hours,
                active_hours_start=hour,
                active_hours_end=23
            )
        )
        await session.commit()
    
    time_str = f"{hour:02d}:{minute:02d}"
    await query.edit_message_text(
        f"✅ Интервал: <b>{minutes} минут</b>\n"
        f"🕐 Первая публикация в: <b>{time_str}</b>\n\n"
        f"💡 Бот будет публиковать посты в {time_str} и далее\n"
        f"каждые {minutes} минут до 23:00.",
        parse_mode="HTML"
    )
    
    context.user_data.pop('temp_project_id', None)
    context.user_data.pop('temp_post_interval', None)
    return ConversationHandler.END


# ============ НАСТРОЙКА ПОДПИСИ ============

def extract_username_from_link(link: str) -> str:
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
    text = text.strip()
    
    if "|" in text:
        parts = text.split("|", 1)
        label = parts[0].strip()
        link = parts[1].strip()
        
        if link:
            if not link.startswith("http"):
                link = "https://" + link
            
            username = extract_username_from_link(link)
            if username:
                if f"@{username}" not in label:
                    label = f"{label} @{username}"
            
            return f'<a href="{link}">{label}</a>'
    
    def replace_link(match):
        full_link = match.group(0)
        username = extract_username_from_link(full_link)
        if username:
            return f'<a href="{full_link}">@{username}</a>'
        return full_link
    
    link_pattern = r'(?:https?://)?t\.me/[a-zA-Z0-9_]+'
    if re.search(link_pattern, text):
        text = re.sub(link_pattern, replace_link, text)
        return text
    
    username_pattern = r'@([a-zA-Z0-9_]+)'
    if re.search(username_pattern, text):
        def make_username_clickable(match):
            username = match.group(1)
            link = f"https://t.me/{username}"
            return f'<a href="{link}">@{username}</a>'
        
        text = re.sub(username_pattern, make_username_clickable, text)
        return text
    
    username = extract_username_from_link(text)
    if username:
        if text.startswith("http"):
            link = text
        else:
            link = f"https://t.me/{username}"
        return f'<a href="{link}">@{username}</a>'
    
    return text


def get_display_text(html_text: str) -> str:
    text = re.sub(r'<a[^>]*>([^<]*)</a>', r'\1', html_text)
    text = re.sub(r'<[^>]+>', '', text)
    return text


async def set_signature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    if not project:
        return ConversationHandler.END
    
    current = project.signature or "не установлена"
    current_display = get_display_text(current) if current != "не установлена" else current
    
    context.user_data['temp_project_id'] = project.id
    
    await update.message.reply_text(
        f"✍️ <b>Подпись проекта «{project.name}»</b>\n\n"
        f"<b>Текущая подпись:</b> {current_display}\n\n"
        f"<b>Введите подпись:</b>\n\n"
        f"📝 <b>Просто текст:</b>\n"
        f"   <code>Мой канал</code>\n\n"
        f"🔗 <b>Текст + ссылка (через | ):</b>\n"
        f"   <code>Мой канал | https://t.me/username</code>\n\n"
        f"🔗 <b>Текст со ссылкой или @username:</b>\n"
        f"   <code>Сделано в https://t.me/username</code>\n"
        f"   <code>Сделано в @username</code>\n"
        f"   <i>Бот сам сделает ссылку кликабельной</i>\n\n"
        f"🔗 <b>Только ссылка:</b>\n"
        f"   <code>https://t.me/username</code>\n\n"
        f"Отправьте <code>удалить</code> чтобы убрать подпись.\n"
        f"/cancel — отмена",
        parse_mode="HTML"
    )
    return AWAITING_SIGNATURE


async def set_signature_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    project_id = context.user_data.get('temp_project_id')
    
    if text.lower() == "удалить":
        signature = None
        reply = "✅ Подпись удалена"
    else:
        signature = parse_signature_input(text)
        display_text = get_display_text(signature)
        
        reply = (
            f"✅ <b>Подпись установлена!</b>\n\n"
            f"<b>В посте будет выглядеть так:</b>\n"
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