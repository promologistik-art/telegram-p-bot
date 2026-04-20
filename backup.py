import os
import shutil
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from config import Config

logger = logging.getLogger(__name__)


class BackupService:
    def __init__(self):
        self.backup_dir = Path(Config.BACKUP_DIR)
        self.db_path = Path(Config.DB_PATH)
        self.max_backups = 7  # Хранить последние 7 бэкапов
        
    def create_backup(self) -> str:
        """Создать бэкап базы данных."""
        if not self.db_path.exists():
            logger.error(f"Database file not found: {self.db_path}")
            return None
        
        # Создаём имя файла с датой
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"bot_backup_{timestamp}.db"
        backup_path = self.backup_dir / backup_name
        
        try:
            # Копируем файл базы данных
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"✅ Backup created: {backup_name}")
            
            # Удаляем старые бэкапы
            self._cleanup_old_backups()
            
            return str(backup_path)
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return None
    
    def _cleanup_old_backups(self):
        """Удалить старые бэкапы, оставить только последние max_backups."""
        try:
            backups = sorted(self.backup_dir.glob("bot_backup_*.db"))
            
            if len(backups) > self.max_backups:
                for old_backup in backups[:-self.max_backups]:
                    old_backup.unlink()
                    logger.info(f"🗑️ Deleted old backup: {old_backup.name}")
        except Exception as e:
            logger.error(f"Failed to cleanup old backups: {e}")
    
    def restore_backup(self, backup_path: str) -> bool:
        """Восстановить базу данных из бэкапа."""
        backup_file = Path(backup_path)
        
        if not backup_file.exists():
            logger.error(f"Backup file not found: {backup_path}")
            return False
        
        try:
            # Создаём бэкап текущей базы перед восстановлением
            if self.db_path.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                pre_restore_backup = self.backup_dir / f"pre_restore_{timestamp}.db"
                shutil.copy2(self.db_path, pre_restore_backup)
                logger.info(f"📦 Pre-restore backup created: {pre_restore_backup.name}")
            
            # Восстанавливаем из бэкапа
            shutil.copy2(backup_file, self.db_path)
            logger.info(f"✅ Database restored from: {backup_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}")
            return False
    
    def list_backups(self) -> list:
        """Получить список всех бэкапов."""
        try:
            backups = sorted(self.backup_dir.glob("bot_backup_*.db"), reverse=True)
            
            backup_list = []
            for backup in backups:
                stat = backup.stat()
                size_mb = stat.st_size / (1024 * 1024)
                created = datetime.fromtimestamp(stat.st_mtime)
                
                backup_list.append({
                    "name": backup.name,
                    "path": str(backup),
                    "size_mb": round(size_mb, 2),
                    "created": created.strftime("%d.%m.%Y %H:%M:%S"),
                    "timestamp": stat.st_mtime
                })
            
            return backup_list
        except Exception as e:
            logger.error(f"Failed to list backups: {e}")
            return []
    
    def get_backup_info(self, backup_name: str) -> dict:
        """Получить информацию о конкретном бэкапе."""
        backup_path = self.backup_dir / backup_name
        
        if not backup_path.exists():
            return None
        
        stat = backup_path.stat()
        return {
            "name": backup_name,
            "path": str(backup_path),
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M:%S")
        }
    
    def delete_backup(self, backup_name: str) -> bool:
        """Удалить конкретный бэкап."""
        backup_path = self.backup_dir / backup_name
        
        if not backup_path.exists():
            return False
        
        try:
            backup_path.unlink()
            logger.info(f"🗑️ Backup deleted: {backup_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete backup: {e}")
            return False


class AutoBackup:
    """Автоматическое создание бэкапов по расписанию."""
    
    def __init__(self, backup_service: BackupService):
        self.backup_service = backup_service
        self._running = False
        self._task = None
    
    async def start(self):
        """Запустить авто-бэкап."""
        self._running = True
        self._task = asyncio.create_task(self._backup_loop())
        logger.info("🟢 AutoBackup started (daily at 03:00 MSK)")
    
    async def _backup_loop(self):
        """Цикл создания бэкапов."""
        while self._running:
            try:
                # Ждём до 3:00 MSK
                await self._wait_until_backup_time()
                
                if self._running:
                    # Создаём бэкап
                    backup_path = self.backup_service.create_backup()
                    if backup_path:
                        logger.info(f"📦 Daily backup created: {backup_path}")
                    
                    # Ждём минуту, чтобы не создавать несколько бэкапов
                    await asyncio.sleep(60)
                    
            except Exception as e:
                logger.error(f"AutoBackup error: {e}")
                await asyncio.sleep(3600)  # Ждём час при ошибке
    
    async def _wait_until_backup_time(self):
        """Ждать до 3:00 MSK."""
        from utils import get_moscow_time
        
        while self._running:
            now = get_moscow_time()
            
            # Целевое время: сегодня в 3:00
            target = now.replace(hour=3, minute=0, second=0, microsecond=0)
            
            if now >= target:
                # Если уже прошло 3:00, ждём до завтра
                target = target + timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            
            if wait_seconds > 0:
                logger.debug(f"Next backup in {wait_seconds / 3600:.1f} hours")
                await asyncio.sleep(min(wait_seconds, 3600))  # Проверяем каждый час
            else:
                break
    
    async def stop(self):
        """Остановить авто-бэкап."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("🔴 AutoBackup stopped")


# Импорт для timedelta
from datetime import timedelta