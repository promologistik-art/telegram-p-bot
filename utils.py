import re
from typing import Optional
from datetime import datetime, timedelta
import pytz

def extract_channel_username(text: str) -> Optional[str]:
    """Извлечь username канала из текста или ссылки."""
    patterns = [
        r'(?:https?://)?t(?:elegram)?\.me/([a-zA-Z0-9_]+)',
        r'@([a-zA-Z0-9_]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def calculate_score(post: dict, criteria: dict) -> int:
    """
    Расчет очков поста на основе критериев.
    Возвращает -1 если пост не проходит по минимальным критериям.
    """
    if not criteria:
        return 1
    
    views = post.get("views", 0)
    reactions = post.get("reactions", 0)
    
    min_views = criteria.get("min_views", 0)
    min_reactions = criteria.get("min_reactions", 0)
    
    if min_views and views < min_views:
        return -1
    
    if min_reactions and reactions < min_reactions:
        return -1
    
    score = 0
    
    if min_views:
        score += (views // 1000) * 10
    
    if min_reactions:
        score += reactions
    
    if post.get("has_media", False):
        score += 5
    
    if score == 0:
        return 1
    
    return score


def clean_caption(text: str) -> str:
    """
    Очистить текст от упоминаний и ссылок.
    Сохраняет нормальные пробелы между предложениями.
    """
    if not text:
        return ""
    
    # Удаляем @упоминания
    text = re.sub(r'@\w+', '', text)
    
    # Удаляем t.me ссылки
    text = re.sub(r'(https?://)?t\.me/\S+', '', text)
    
    # Удаляем http ссылки
    text = re.sub(r'https?://\S+', '', text)
    
    # Убираем HTML-теги если есть
    text = re.sub(r'<[^>]+>', '', text)
    
    # Сохраняем структуру переносов
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    # Убираем множественные пробелы (но не между точкой и словом)
    text = re.sub(r' +', ' ', text)
    
    # Исправляем слипшиеся предложения (точка + слово без пробела)
    text = re.sub(r'\.([А-ЯA-Z])', r'. \1', text)
    text = re.sub(r'\!([А-ЯA-Z])', r'! \1', text)
    text = re.sub(r'\?([А-ЯA-Z])', r'? \1', text)
    
    text = text.strip()
    
    # Обрезаем до 1024 символов
    if len(text) > 1024:
        text = text[:1021] + "..."
    
    return text


def calculate_next_post_time(project) -> Optional[datetime]:
    """Рассчитать время следующей публикации с учётом расписания."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    now_moscow = datetime.now(moscow_tz)
    
    current_hour = now_moscow.hour
    
    if current_hour < project.active_hours_start:
        next_time = now_moscow.replace(
            hour=project.active_hours_start,
            minute=0,
            second=0,
            microsecond=0
        )
        return next_time
    
    if current_hour >= project.active_hours_end:
        next_time = now_moscow.replace(
            hour=project.active_hours_start,
            minute=0,
            second=0,
            microsecond=0
        ) + timedelta(days=1)
        return next_time
    
    next_time = now_moscow + timedelta(hours=project.post_interval_hours)
    
    if next_time.hour >= project.active_hours_end:
        next_time = now_moscow.replace(
            hour=project.active_hours_start,
            minute=0,
            second=0,
            microsecond=0
        ) + timedelta(days=1)
    
    return next_time


def get_moscow_time() -> datetime:
    """Получить текущее время в Москве."""
    moscow_tz = pytz.timezone("Europe/Moscow")
    return datetime.now(moscow_tz)


def format_datetime(dt: datetime) -> str:
    """Форматировать дату и время для отображения."""
    if not dt:
        return "никогда"
    
    moscow_tz = pytz.timezone("Europe/Moscow")
    if dt.tzinfo is None:
        dt = moscow_tz.localize(dt)
    
    return dt.strftime("%d.%m.%Y %H:%M")


def format_number(num: int) -> str:
    """Форматировать число с разделителями."""
    if num >= 1000000:
        return f"{num/1000000:.1f}M"
    elif num >= 1000:
        return f"{num/1000:.1f}K"
    return str(num)


def parse_number(text: str) -> int:
    """Парсинг чисел с K, M."""
    if not text:
        return 0
    
    text = str(text).strip().upper().replace(" ", "")
    text = text.replace(",", ".")
    
    if "K" in text:
        return int(float(text.replace("K", "")) * 1000)
    elif "M" in text:
        return int(float(text.replace("M", "")) * 1000000)
    else:
        try:
            clean = re.sub(r'[^\d.]', '', text)
            if clean:
                return int(float(clean))
        except:
            pass
    
    return 0