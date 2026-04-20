import logging
from telegram import Update
from telegram.ext import ContextTypes
from scraper import TelegramScraper
from utils import format_number

logger = logging.getLogger(__name__)


async def test_scraper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ℹ️ /test [username]")
        return
    
    username = context.args[0].replace("@", "")
    msg = await update.message.reply_text(f"🔍 Тестирую @{username}...")
    
    async with TelegramScraper() as scraper:
        info = await scraper.get_channel_info(username)
        if not info:
            await msg.edit_text("❌ Канал не найден")
            return
        
        posts = await scraper.get_posts(username, limit=5)
        
        if posts:
            text = f"📨 @{username}\nНайдено: {len(posts)}\n\n"
            for p in posts[:5]:
                text += f"👁 {format_number(p['views'])} | ❤️ {format_number(p['reactions'])}\n"
                text += f"📎 {'📷' if p.get('media_type') == 'photo' else '🎬' if p.get('media_type') == 'video' else '📝'}\n\n"
        else:
            text = "❌ Посты не найдены"
    
    await msg.edit_text(text)