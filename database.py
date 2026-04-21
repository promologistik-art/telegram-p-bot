import os
import logging
import shutil
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, text
from datetime import datetime, timedelta
from config import Config
from models import Base, User, Project, SourceChannel, TargetChannel, ParsedPost

logger = logging.getLogger(__name__)

os.makedirs(Config.DATA_DIR, exist_ok=True)
os.makedirs(Config.TEMP_DIR, exist_ok=True)
os.makedirs(Config.BACKUP_DIR, exist_ok=True)

old_db_path = "bot.db"
if os.path.exists(old_db_path) and not os.path.exists(Config.DB_PATH):
    shutil.move(old_db_path, Config.DB_PATH)
    logger.info(f"Moved database from {old_db_path} to {Config.DB_PATH}")

engine = create_async_engine(f"sqlite+aiosqlite:///{Config.DB_PATH}", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Кэш спарсенных постов: project_id -> set of urls
parsed_urls = {}


async def migrate_to_projects():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
        )
        if not result.scalar():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Created new tables")
            
            result = await session.execute(select(User))
            users = result.scalars().all()
            
            for user in users:
                result = await session.execute(
                    select(SourceChannel).where(SourceChannel.user_id == user.telegram_id)
                )
                old_sources = result.scalars().all()
                
                result = await session.execute(
                    select(TargetChannel).where(TargetChannel.user_id == user.telegram_id)
                )
                old_targets = result.scalars().all()
                
                if old_sources or old_targets:
                    project = Project(
                        user_id=user.telegram_id,
                        name="Основной",
                        check_interval_minutes=user.check_interval_minutes if hasattr(user, 'check_interval_minutes') else 60
                    )
                    session.add(project)
                    await session.flush()
                    
                    for source in old_sources:
                        source.project_id = project.id
                    
                    for target in old_targets:
                        target.project_id = project.id
                    
                    logger.info(f"Migrated user {user.telegram_id}")
            
            await session.commit()
            logger.info("Migration completed")
        
        # Добавляем новые поля
        for field, default in [
            ("max_projects", "1"),
            ("max_sources_per_project", "3"),
            ("signature", "TEXT"),
            ("trial_ends_at", "TIMESTAMP"),
            ("subscription_active", "FALSE"),
            ("subscription_ends_at", "TIMESTAMP"),
            ("tariff", "'trial'"),
            ("min_post_interval_minutes", "120"),
            ("min_check_interval_minutes", "60"),
            ("last_trial_warning_sent", "TIMESTAMP"),
            ("last_subscription_warning_sent", "TIMESTAMP"),
        ]:
            try:
                await session.execute(text(f"ALTER TABLE users ADD COLUMN {field} {default}"))
            except:
                pass
        
        try:
            await session.execute(text("ALTER TABLE projects ADD COLUMN signature TEXT"))
        except:
            pass
        
        # Миграция parsed_posts — добавляем project_id
        try:
            await session.execute(text("ALTER TABLE parsed_posts ADD COLUMN project_id INTEGER REFERENCES projects(id)"))
        except:
            pass
        
        # Заполняем project_id для существующих записей
        await session.execute(text("""
            UPDATE parsed_posts 
            SET project_id = (
                SELECT project_id FROM source_channels 
                WHERE source_channels.id = parsed_posts.source_channel_id
            )
            WHERE project_id IS NULL
        """))
        
        # Удаляем старый уникальный constraint если есть
        try:
            await session.execute(text("DROP INDEX IF EXISTS idx_parsed_posts_post_url"))
        except:
            pass
        
        # Создаём новый составной уникальный constraint
        try:
            await session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_project_post ON parsed_posts(project_id, post_url)"))
        except:
            pass
        
        await session.commit()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    await migrate_to_projects()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == Config.ADMIN_ID))
        admin = result.scalar_one_or_none()
        if not admin:
            admin = User(
                telegram_id=Config.ADMIN_ID,
                is_admin=True,
                tariff="unlimited",
                max_projects=999,
                max_sources_per_project=999,
                min_post_interval_minutes=1,
                min_check_interval_minutes=5,
                subscription_active=True,
                trial_ends_at=datetime.utcnow() + timedelta(days=36500)
            )
            session.add(admin)
            await session.commit()
            logger.info(f"Admin user {Config.ADMIN_ID} created")
        
        result = await session.execute(
            select(Project).where(Project.user_id == Config.ADMIN_ID).order_by(Project.id)
        )
        admin_projects = result.scalars().all()
        
        if not admin_projects:
            admin_project = Project(
                user_id=Config.ADMIN_ID,
                name="Админский",
                check_interval_minutes=60,
                post_interval_hours=2,
                active_hours_start=8,
                active_hours_end=22
            )
            session.add(admin_project)
            await session.commit()
            logger.info("Admin project created")


async def is_post_parsed(project_id: int, post_url: str) -> bool:
    cache_key = f"{project_id}:{post_url}"
    if cache_key in parsed_urls:
        return True
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ParsedPost).where(
                ParsedPost.project_id == project_id,
                ParsedPost.post_url == post_url
            )
        )
        exists = result.scalar_one_or_none() is not None
        if exists:
            parsed_urls[cache_key] = True
        return exists


async def mark_post_parsed(project_id: int, source_channel_id: int, post_url: str):
    cache_key = f"{project_id}:{post_url}"
    parsed_urls[cache_key] = True
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ParsedPost).where(
                ParsedPost.project_id == project_id,
                ParsedPost.post_url == post_url
            )
        )
        if result.scalar_one_or_none():
            return
        
        post = ParsedPost(
            project_id=project_id,
            source_channel_id=source_channel_id,
            post_url=post_url
        )
        session.add(post)
        try:
            await session.commit()
        except:
            await session.rollback()


async def clear_parsed_cache():
    parsed_urls.clear()


async def get_active_projects():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Project).where(Project.is_active == True))
        return result.scalars().all()


async def get_user_projects(telegram_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Project).where(Project.user_id == telegram_id, Project.is_active == True)
        )
        return result.scalars().all()


async def get_project_sources(project_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SourceChannel).where(
                SourceChannel.project_id == project_id,
                SourceChannel.is_active == True
            )
        )
        return result.scalars().all()


async def get_project_target(project_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TargetChannel).where(
                TargetChannel.project_id == project_id,
                TargetChannel.is_active == True
            )
        )
        return result.scalar_one_or_none()