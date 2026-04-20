import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, delete
from database import AsyncSessionLocal
from models import User, SourceChannel
from scraper import TelegramScraper
from utils import extract_channel_username
from .utils import require_project, get_sources_count, get_project_target, send_project_ready_message
from .constants import AWAITING_SOURCE_USERNAME, AWAITING_CRITERIA, AWAITING_VIEWS, AWAITING_REACTIONS

logger = logging.getLogger(__name__)


async def add_source_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one()
        
        count = await get_sources_count(project.id)
        if count >= user.max_sources_per_project:
            await update.message.reply_text(
                f"❌ Достигнут лимит источников ({user.max_sources_per_project})."
            )
            return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    context.user_data['temp_project_name'] = project.name
    
    await update.message.reply_text(
        f"📥 Добавление источника в «{project.name}»\n\n"
        "Отправьте username канала (@name) или ссылку:\n"
        "• @durov\n"
        "• https://t.me/durov"
    )
    return AWAITING_SOURCE_USERNAME


async def add_source_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = extract_channel_username(update.message.text)
    if not username:
        await update.message.reply_text("❌ Не удалось распознать username. Попробуйте ещё раз.")
        return AWAITING_SOURCE_USERNAME
    
    logger.info(f"Checking channel: @{username}")
    
    async with TelegramScraper() as scraper:
        info = await scraper.get_channel_info(username)
    
    if not info:
        await update.message.reply_text(
            "❌ Канал не найден или не является публичным.\n"
            "Попробуйте другой канал или проверьте правильность username."
        )
        return AWAITING_SOURCE_USERNAME
    
    logger.info(f"Channel found: @{username} - {info['title']}")
    
    context.user_data['temp_source'] = {
        'username': username,
        'title': info['title'],
        'project_id': context.user_data.get('temp_project_id'),
        'project_name': context.user_data.get('temp_project_name')
    }
    
    keyboard = [
        [InlineKeyboardButton("🎯 Свои критерии", callback_data="criteria_custom")],
        [InlineKeyboardButton("👁 1000+ просмотров", callback_data="criteria_views")],
        [InlineKeyboardButton("❤️ 50+ реакций", callback_data="criteria_reactions")],
        [InlineKeyboardButton("👁+❤️ 500+ и 25+", callback_data="criteria_both")],
        [InlineKeyboardButton("⚡ Без критериев", callback_data="criteria_none")],
    ]
    
    await update.message.reply_text(
        f"✅ Канал: @{username}\n"
        f"📝 Название: {info['title']}\n\n"
        f"Выберите критерии отбора:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return AWAITING_CRITERIA


async def add_source_criteria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("criteria_", "")
    temp = context.user_data.get('temp_source')
    
    if not temp:
        await query.edit_message_text("❌ Ошибка: данные не найдены. Начните заново с /add_source")
        return ConversationHandler.END
    
    logger.info(f"Criteria choice: {choice} for @{temp['username']}")
    
    if choice == "custom":
        await query.edit_message_text(
            "📊 <b>Настройка критериев</b>\n\n"
            "Введите минимальное количество просмотров (0 = не учитывать):",
            parse_mode="HTML"
        )
        context.user_data['awaiting_criteria'] = 'views'
        return AWAITING_VIEWS
    else:
        criteria = {
            "views": {"min_views": 1000},
            "reactions": {"min_reactions": 50},
            "both": {"min_views": 500, "min_reactions": 25},
            "none": {}
        }.get(choice, {})
        
        await save_source_with_criteria(query, context, temp, criteria)
        return ConversationHandler.END


async def criteria_views_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        views = int(update.message.text.strip())
        if views < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return AWAITING_VIEWS
    
    context.user_data['temp_criteria_views'] = views
    await update.message.reply_text("📊 Введите минимальное количество реакций (0 = не учитывать):")
    return AWAITING_REACTIONS


async def criteria_reactions_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reactions = int(update.message.text.strip())
        if reactions < 0:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите целое число (0 = не учитывать):")
        return AWAITING_REACTIONS
    
    views = context.user_data.get('temp_criteria_views', 0)
    criteria = {}
    if views > 0:
        criteria['min_views'] = views
    if reactions > 0:
        criteria['min_reactions'] = reactions
    
    temp = context.user_data.get('temp_source')
    if not temp:
        await update.message.reply_text("❌ Ошибка: данные не найдены. Начните заново с /add_source")
        return ConversationHandler.END
    
    await save_source_with_criteria(update, context, temp, criteria)
    
    context.user_data.pop('temp_criteria_views', None)
    context.user_data.pop('awaiting_criteria', None)
    return ConversationHandler.END


async def save_source_with_criteria(target, context, temp: dict, criteria: dict):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SourceChannel).where(
                SourceChannel.project_id == temp['project_id'],
                SourceChannel.channel_username == temp['username']
            )
        )
        if result.scalar_one_or_none():
            text = f"⚠️ Канал @{temp['username']} уже добавлен в этот проект."
            if hasattr(target, 'edit_message_text'):
                await target.edit_message_text(text)
            else:
                await target.message.reply_text(text)
            return
        
        channel = SourceChannel(
            project_id=temp['project_id'],
            channel_username=temp['username'],
            channel_title=temp['title'],
            criteria=criteria
        )
        session.add(channel)
        await session.commit()
        logger.info(f"Added source @{temp['username']} to project {temp['project_id']}")
    
    criteria_text = []
    if criteria.get('min_views'):
        criteria_text.append(f"👁 ≥{criteria['min_views']}")
    if criteria.get('min_reactions'):
        criteria_text.append(f"❤️ ≥{criteria['min_reactions']}")
    criteria_str = ", ".join(criteria_text) if criteria_text else "без критериев"
    
    text = f"✅ Канал @{temp['username']} добавлен!\n📋 Критерии: {criteria_str}"
    
    if hasattr(target, 'edit_message_text'):
        await target.edit_message_text(text)
    else:
        await target.message.reply_text(text)
    
    project_id = temp['project_id']
    project_name = temp['project_name']
    
    context.user_data.pop('temp_source', None)
    context.user_data.pop('temp_project_id', None)
    context.user_data.pop('temp_project_name', None)
    
    sources_count = await get_sources_count(project_id)
    target_channel = await get_project_target(project_id)
    if sources_count == 1 and target_channel:
        if hasattr(target, 'message'):
            await send_project_ready_message(target, project_name)
        elif hasattr(target, 'edit_message_text'):
            await target.message.reply_text(
                f"✅ <b>Проект «{project_name}» готов к работе!</b>\n\n"
                f"• /set_interval — настроить частоту\n"
                f"• /parse — запустить парсинг",
                parse_mode="HTML"
            )


async def my_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    project = await require_project(update, context)
    
    if not project:
        return
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SourceChannel)
            .where(SourceChannel.project_id == project.id)
            .order_by(SourceChannel.added_at.desc())
        )
        sources = result.scalars().all()
        
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one()
    
    if not sources:
        await update.message.reply_text(
            f"📭 В проекте «{project.name}» нет источников.\n"
            f"Добавьте: /add_source"
        )
        return
    
    text = f"📥 <b>Источники «{project.name}»</b> ({len(sources)} / {user.max_sources_per_project})\n\n"
    keyboard = []
    
    for src in sources:
        criteria_text = []
        if src.criteria:
            if "min_views" in src.criteria:
                criteria_text.append(f"👁 ≥{src.criteria['min_views']}")
            if "min_reactions" in src.criteria:
                criteria_text.append(f"❤️ ≥{src.criteria['min_reactions']}")
        criteria_str = ", ".join(criteria_text) if criteria_text else "без критериев"
        
        text += f"• @{src.channel_username}\n"
        text += f"  📊 {criteria_str}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"❌ Удалить @{src.channel_username}", callback_data=f"del_source_{src.id}")
        ])
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        parse_mode="HTML"
    )


async def delete_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    source_id = int(query.data.replace("del_source_", ""))
    
    async with AsyncSessionLocal() as session:
        await session.execute(delete(SourceChannel).where(SourceChannel.id == source_id))
        await session.commit()
        logger.info(f"Deleted source {source_id}")
    
    await query.edit_message_text("✅ Источник удалён")