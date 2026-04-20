import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import delete
from database import AsyncSessionLocal
from models import TargetChannel
from .utils import require_project, get_sources_count, get_project_target, send_project_ready_message
from .constants import AWAITING_TARGET_FORWARD

logger = logging.getLogger(__name__)


async def add_target_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    
    if not project:
        return ConversationHandler.END
    
    target = await get_project_target(project.id)
    if target:
        await update.message.reply_text(
            f"⚠️ В проекте уже есть целевой канал: {target.channel_title}\n"
            f"Удалите его через /my_targets"
        )
        return ConversationHandler.END
    
    context.user_data['temp_project_id'] = project.id
    context.user_data['temp_project_name'] = project.name
    
    me = await context.bot.get_me()
    await update.message.reply_text(
        f"🎯 Добавление целевого канала в «{project.name}»\n\n"
        f"1. Добавьте @{me.username} в админы канала\n"
        f"2. Выдайте права на публикацию\n"
        f"3. Перешлите сюда любое сообщение из канала"
    )
    return AWAITING_TARGET_FORWARD


async def add_target_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    if not msg.forward_from_chat or msg.forward_from_chat.type != 'channel':
        await update.message.reply_text("❌ Перешлите сообщение из канала.")
        return AWAITING_TARGET_FORWARD
    
    chat = msg.forward_from_chat
    project_id = context.user_data.get('temp_project_id')
    project_name = context.user_data.get('temp_project_name')
    
    try:
        test_msg = await context.bot.send_message(chat.id, "🔧 Проверка прав...")
        await test_msg.delete()
    except Exception as e:
        await update.message.reply_text("❌ Бот не имеет прав администратора.")
        return AWAITING_TARGET_FORWARD
    
    async with AsyncSessionLocal() as session:
        channel = TargetChannel(
            project_id=project_id,
            channel_id=chat.id,
            channel_username=chat.username,
            channel_title=chat.title
        )
        session.add(channel)
        await session.commit()
    
    await update.message.reply_text(
        f"✅ Целевой канал добавлен!\n"
        f"📝 {chat.title}"
    )
    
    context.user_data.pop('temp_project_id', None)
    context.user_data.pop('temp_project_name', None)
    
    sources_count = await get_sources_count(project_id)
    if sources_count > 0:
        await send_project_ready_message(update, project_name)
    
    return ConversationHandler.END


async def my_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = await require_project(update, context)
    
    if not project:
        return
    
    target = await get_project_target(project.id)
    
    if not target:
        await update.message.reply_text(
            f"📭 В проекте «{project.name}» нет целевого канала.\n"
            f"Добавьте: /add_target"
        )
        return
    
    text = f"🎯 <b>Целевой канал «{project.name}»</b>\n\n"
    text += f"📝 {target.channel_title}\n"
    if target.channel_username:
        text += f"🔗 @{target.channel_username}\n"
    text += f"🆔 {target.channel_id}\n"
    
    keyboard = [[InlineKeyboardButton("❌ Удалить", callback_data=f"del_target_{target.id}")]]
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def delete_target_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    target_id = int(query.data.replace("del_target_", ""))
    
    async with AsyncSessionLocal() as session:
        await session.execute(delete(TargetChannel).where(TargetChannel.id == target_id))
        await session.commit()
    
    await query.edit_message_text("✅ Целевой канал удалён")