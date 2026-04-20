import os
import logging
import shutil
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, text
from config import Config
from models import Base, User, Project, SourceChannel, TargetChannel

logger = logging.getLogger(__name__)

# Создаём все необходимые папки
os.makedirs(Config.DATA_DIR, exist_ok=True)
os.makedirs(Config.TEMP_DIR, exist_ok=True)
os.makedirs(Config.BACKUP_DIR, exist_ok=True)

# Проверяем, есть ли старая БД в корне, и переносим в data/
old_db_path = "bot.db"
if os.path.exists(old_db_path) and not os.path.exists(Config.DB_PATH):
    shutil.move(old_db_path, Config.DB_PATH)
    logger.info(f"Moved database from {old_db_path} to {Config.DB_PATH}")

engine = create_async_engine(f"sqlite+aiosqlite:///{Config.DB_PATH}", echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

parsed_urls = set()


async def migrate_to_projects():
    """Автоматическая миграция старых данных в новую структуру проектов."""
    
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
                    
                    logger.info(f"Migrated user {user.telegram_id}: {len(old_sources)} sources, {len(old_targets)} targets")
            
            await session.commit()
            logger.info("Migration completed")
        
        try:
            await session.execute(text("ALTER TABLE users ADD COLUMN max_projects INTEGER DEFAULT 1"))
        except:
            pass
        
        try:
            await session.execute(text("ALTER TABLE users ADD COLUMN max_sources_per_project INTEGER DEFAULT 3"))
        except:
            pass
        
        try:
            await session.execute(text("ALTER TABLE projects ADD COLUMN signature TEXT"))
        except:
            pass
        
        await session.commit()


async def init_db():
    """Инициализация базы данных с авто-миграцией."""
    
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
                max_projects=999,
                max_sources_per_project=999
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
        else:
            logger.info(f"Admin has {len(admin_projects)} project(s)")


async def is_post_parsed(post_url: str) -> bool:
    if post_url in parsed_urls:
        return True
    async with AsyncSessionLocal() as session:
        from models import ParsedPost
        result = await session.execute(select(ParsedPost).where(ParsedPost.post_url == post_url))
        exists = result.scalar_one_or_none() is not None
        if exists:
            parsed_urls.add(post_url)
        return exists


async def mark_post_parsed(source_channel_id: int, post_url: str):
    parsed_urls.add(post_url)
    async with AsyncSessionLocal() as session:
        from models import ParsedPost
        post = ParsedPost(source_channel_id=source_channel_id, post_url=post_url)
        session.add(post)
        await session.commit()


async def get_active_projects():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Project).where(Project.is_active == True)
        )
        return result.scalars().all()


async def get_user_projects(telegram_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Project).where(
                Project.user_id == telegram_id,
                Project.is_active == True
            )
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