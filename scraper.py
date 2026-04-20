import aiohttp
import asyncio
import re
import logging
import json
from typing import Optional, List, Dict
from bs4 import BeautifulSoup
from config import Config
from utils import parse_number

logger = logging.getLogger(__name__)

class TelegramScraper:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        headers = {
            "User-Agent": Config.SCRAPER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        self.session = aiohttp.ClientSession(headers=headers)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _fetch(self, url: str) -> Optional[str]:
        for attempt in range(Config.SCRAPER_RETRIES):
            try:
                async with self.session.get(url, timeout=Config.SCRAPER_TIMEOUT) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    else:
                        logger.warning(f"HTTP {resp.status} for {url}")
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                await asyncio.sleep(2)
        return None

    async def get_channel_info(self, username: str) -> Optional[Dict]:
        url = f"https://t.me/{username}"
        html = await self._fetch(url)
        if not html:
            return None
        
        soup = BeautifulSoup(html, "lxml")
        
        title_tag = soup.find("meta", property="og:title")
        title = title_tag["content"] if title_tag else username
        title = title.replace("Telegram: Contact @", "").strip()
        
        desc_tag = soup.find("meta", property="og:description")
        description = desc_tag["content"] if desc_tag else ""
        
        img_tag = soup.find("meta", property="og:image")
        avatar = img_tag["content"] if img_tag else None
        
        return {
            "username": username,
            "title": title,
            "description": description,
            "avatar": avatar
        }

    async def get_posts(self, username: str, limit: int = 10) -> List[Dict]:
        url = f"https://t.me/s/{username}"
        html = await self._fetch(url)
        if not html:
            logger.error(f"Failed to fetch {url}")
            return []
        
        soup = BeautifulSoup(html, "lxml")
        posts = []
        
        messages = soup.find_all("div", class_="tgme_widget_message")
        logger.debug(f"Found {len(messages)} messages for @{username}")
        
        for msg_div in messages[:limit]:
            try:
                post = self._parse_message(msg_div, username)
                if post:
                    posts.append(post)
            except Exception as e:
                logger.error(f"Parse error for @{username}: {e}")
                continue
        
        posts.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        return posts

    def _parse_message(self, msg_div, username: str) -> Optional[Dict]:
        data_post = msg_div.get("data-post")
        if not data_post:
            return None
        
        parts = data_post.split("/")
        if len(parts) < 2:
            return None
        
        message_id = parts[1]
        post_url = f"https://t.me/{username}/{message_id}"
        
        post_datetime = ""
        time_tag = msg_div.find("time")
        if time_tag and time_tag.has_attr("datetime"):
            post_datetime = time_tag["datetime"]
        
        text_div = msg_div.find("div", class_="tgme_widget_message_text")
        text = text_div.get_text(strip=False) if text_div else ""
        
        views = 0
        views_span = msg_div.find("span", class_="tgme_widget_message_views")
        if views_span:
            views_text = views_span.get_text(strip=True)
            views = parse_number(views_text)
        
        reactions = self._parse_reactions(msg_div)
        
        # УЛУЧШЕННЫЙ ПАРСИНГ МЕДИА
        has_photo = False
        has_video = False
        media_url = None
        media_type = None
        
        # Способ 1: Одиночное фото
        photo_wrap = msg_div.find("a", class_="tgme_widget_message_photo_wrap")
        if photo_wrap:
            has_photo = True
            media_type = "photo"
            img = photo_wrap.find("img")
            if img:
                media_url = img.get("src")
            if not media_url:
                style = photo_wrap.get("style", "")
                bg_match = re.search(r"url\('(.+?)'\)", style)
                if bg_match:
                    media_url = bg_match.group(1)
        
        # Способ 2: Галерея (несколько фото)
        if not has_photo:
            gallery = msg_div.find("div", class_="tgme_widget_message_album_wrap")
            if gallery:
                first_photo = gallery.find("a", class_="tgme_widget_message_photo_wrap")
                if first_photo:
                    has_photo = True
                    media_type = "photo"
                    img = first_photo.find("img")
                    if img:
                        media_url = img.get("src")
                    if not media_url:
                        style = first_photo.get("style", "")
                        bg_match = re.search(r"url\('(.+?)'\)", style)
                        if bg_match:
                            media_url = bg_match.group(1)
        
        # Способ 3: Видео
        if not has_photo:
            video = msg_div.find("video")
            if video:
                src = video.get("src", "")
                if src and ("file/" in src or "video/" in src):
                    has_video = True
                    media_type = "video"
                    media_url = src
        
        # Способ 4: Круглое видео
        if not has_photo and not has_video:
            round_video = msg_div.find("video", class_="tgme_widget_message_roundvideo")
            if round_video:
                src = round_video.get("src", "")
                if src:
                    has_video = True
                    media_type = "video"
                    media_url = src
        
        return {
            "url": post_url,
            "message_id": message_id,
            "text": text,
            "views": views,
            "reactions": reactions,
            "has_media": has_photo or has_video,
            "media_url": media_url,
            "media_type": media_type,
            "datetime": post_datetime
        }

    def _parse_reactions(self, msg_div) -> int:
        total = 0
        
        reactions_div = msg_div.find("div", class_="tgme_widget_message_reactions")
        if reactions_div:
            for span in reactions_div.find_all("span"):
                if span.get("class") and "count" in " ".join(span.get("class", [])):
                    count_text = span.get_text(strip=True)
                    total += parse_number(count_text)
            
            if total == 0:
                for elem in reactions_div.find_all(attrs={"data-count": True}):
                    total += parse_number(elem.get("data-count", "0"))
        
        if total == 0:
            scripts = msg_div.find_all("script", type="application/json")
            for script in scripts:
                try:
                    data = json.loads(script.string)
                    total += self._extract_reactions_from_json(data)
                except:
                    pass
        
        return total

    def _extract_reactions_from_json(self, data, depth=0) -> int:
        if depth > 5:
            return 0
        
        total = 0
        
        if isinstance(data, dict):
            for key in ['reactions', 'reaction_count', 'count', 'total_reactions']:
                if key in data:
                    try:
                        if isinstance(data[key], (int, float)):
                            total += int(data[key])
                        elif isinstance(data[key], str):
                            total += parse_number(data[key])
                        elif isinstance(data[key], list):
                            for item in data[key]:
                                if isinstance(item, dict) and 'count' in item:
                                    total += int(item.get('count', 0))
                    except:
                        pass
            
            for value in data.values():
                total += self._extract_reactions_from_json(value, depth + 1)
        
        elif isinstance(data, list):
            for item in data:
                total += self._extract_reactions_from_json(item, depth + 1)
        
        return total

    async def download_media(self, media_url: str, save_path: str) -> bool:
        try:
            async with self.session.get(media_url) as resp:
                if resp.status == 200:
                    with open(save_path, "wb") as f:
                        f.write(await resp.read())
                    return True
        except Exception as e:
            logger.error(f"Download failed: {e}")
        return False