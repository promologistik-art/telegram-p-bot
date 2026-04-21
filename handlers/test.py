import logging
from telegram import Update
from telegram.ext import ContextTypes
from scraper import TelegramScraper
from utils import format_number

logger = logging.getLogger(__name__)


async def test_scraper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ℹ️ /test [username]\nПример: /test durov")
        return
    
    # Очищаем username от ссылок и @
    raw = context.args[0]
    username = raw.replace("@", "").replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").strip("/")
    
    msg = await update.message.reply_text(f"🔍 Тестирую @{username}...")
    
    async with TelegramScraper() as scraper:
        info = await scraper.get_channel_info(username)
        if not info:
            await msg.edit_text(f"❌ Канал @{username} не найден или не публичный")
            return
        
        posts = await scraper.get_posts(username, limit=5)
        
        if posts:
            text = f"📨 @{username} ({info['title']})\n"
            text += f"Найдено постов: {len(posts)}\n\n"
            for i, p in enumerate(posts[:5], 1):
                text += f"{i}. 👁 {format_number(p['views'])} | ❤️ {format_number(p['reactions'])}\n"
                text += f"   📎 {'📷' if p.get('media_type') == 'photo' else '🎬' if p.get('media_type') == 'video' else '📝'}\n"
                if p.get('text'):
                    text += f"   {p['text'][:50]}...\n"
                text += "\n"
        else:
            text = f"❌ Посты не найдены. Проверьте https://t.me/s/{username}"
    
    await msg.edit_text(text)