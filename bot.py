#!/usr/bin/env python3
import asyncio
import logging
import sys
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler
)

from config import Config
from database import init_db
from handlers import (
    start, help_command, cancel,
    my_projects, projects_callback, handle_project_name,
    back_to_projects_callback,
    add_source_start, add_source_username, add_source_criteria,
    criteria_views_input, criteria_reactions_input,
    my_sources, delete_source_callback,
    add_target_start, add_target_forward, my_targets, delete_target_callback,
    set_interval_start, set_interval_callback,
    set_post_interval_start, set_post_interval_callback,
    set_signature_start, set_signature_input,
    status, project_stats,
    parse_now, queue_status, post_now, clear_old_queue, clear_failed_queue,
    admin_panel, admin_callback, admin_back_callback,
    admin_set_tariff_start, admin_extend_trial_start,
    broadcast_start, broadcast_send,
    test_scraper,
    setup_bot_commands,
    AWAITING_SOURCE_USERNAME, AWAITING_TARGET_FORWARD, AWAITING_CRITERIA,
    AWAITING_INTERVAL, AWAITING_VIEWS, AWAITING_REACTIONS, AWAITING_SIGNATURE,
    AWAITING_POST_INTERVAL, AWAITING_TARIFF_SELECT, AWAITING_TARIFF_USERNAME,
    AWAITING_EXTEND_DAYS, AWAITING_BROADCAST_MESSAGE
)

from poster import PosterService
from scheduler import Scheduler
from post_scheduler import PostScheduler
from backup import BackupService, AutoBackup

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def main():
    await init_db()
    logger.info("Database initialized")
    
    app = Application.builder().token(Config.BOT_TOKEN).build()
    await setup_bot_commands(app)
    
    poster = PosterService(app.bot)
    scheduler = Scheduler(poster)
    post_scheduler = PostScheduler(poster)
    
    app.bot_data['scheduler'] = scheduler
    app.bot_data['post_scheduler'] = post_scheduler
    app.bot_data['poster'] = poster
    
    scheduler_task = asyncio.create_task(scheduler.start())
    post_scheduler_task = asyncio.create_task(post_scheduler.start())
    
    backup_service = BackupService()
    auto_backup = AutoBackup(backup_service)
    auto_backup_task = asyncio.create_task(auto_backup.start())
    
    # ============ Conversation Handlers ============
    
    add_source_conv = ConversationHandler(
        entry_points=[CommandHandler("add_source", add_source_start)],
        states={
            AWAITING_SOURCE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_source_username)],
            AWAITING_CRITERIA: [CallbackQueryHandler(add_source_criteria, pattern="^criteria_")],
            AWAITING_VIEWS: [MessageHandler(filters.TEXT & ~filters.COMMAND, criteria_views_input)],
            AWAITING_REACTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, criteria_reactions_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    add_target_conv = ConversationHandler(
        entry_points=[CommandHandler("add_target", add_target_start)],
        states={
            AWAITING_TARGET_FORWARD: [MessageHandler(filters.FORWARDED, add_target_forward)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    set_interval_conv = ConversationHandler(
        entry_points=[CommandHandler("set_interval", set_interval_start)],
        states={
            AWAITING_INTERVAL: [CallbackQueryHandler(set_interval_callback, pattern="^interval_")]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    set_post_interval_conv = ConversationHandler(
        entry_points=[CommandHandler("set_post_interval", set_post_interval_start)],
        states={
            AWAITING_POST_INTERVAL: [CallbackQueryHandler(set_post_interval_callback, pattern="^post_")]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    set_signature_conv = ConversationHandler(
        entry_points=[CommandHandler("set_signature", set_signature_start)],
        states={
            AWAITING_SIGNATURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_signature_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Админские диалоги
    set_tariff_conv = ConversationHandler(
        entry_points=[CommandHandler("admin_set_tariff", admin_set_tariff_start)],
        states={
            AWAITING_TARIFF_SELECT: [CallbackQueryHandler(admin_callback, pattern="^tariff_set_")],
            AWAITING_TARIFF_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_callback)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            AWAITING_BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # ============ Command Handlers ============
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("my_projects", my_projects))
    app.add_handler(CommandHandler("my_sources", my_sources))
    app.add_handler(CommandHandler("my_targets", my_targets))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("project_stats", project_stats))
    app.add_handler(CommandHandler("parse", parse_now))
    app.add_handler(CommandHandler("queue", queue_status))
    app.add_handler(CommandHandler("postnow", post_now))
    app.add_handler(CommandHandler("clear_queue", clear_old_queue))
    app.add_handler(CommandHandler("clear_failed", clear_failed_queue))
    app.add_handler(CommandHandler("test", test_scraper))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("admin_extend_trial", admin_extend_trial_start))
    
    # ============ Conversation Handlers (register) ============
    
    app.add_handler(add_source_conv)
    app.add_handler(add_target_conv)
    app.add_handler(set_interval_conv)
    app.add_handler(set_post_interval_conv)
    app.add_handler(set_signature_conv)
    app.add_handler(set_tariff_conv)
    app.add_handler(broadcast_conv)
    
    # ============ Message Handlers ============
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_project_name))
    
    # ============ Callback Handlers ============
    
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^tariff_set_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^user_tariff_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^extend_user_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^deactivate_user_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^activate_user_"))
    app.add_handler(CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(projects_callback, pattern="^(create_project|select_project_|delete_project_|confirm_delete_|cancel_delete|stats_project_|settings_project_)"))
    app.add_handler(CallbackQueryHandler(back_to_projects_callback, pattern="^back_to_projects$"))
    app.add_handler(CallbackQueryHandler(delete_source_callback, pattern="^del_source_"))
    app.add_handler(CallbackQueryHandler(delete_target_callback, pattern="^del_target_"))
    
    # ============ Запуск ============
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query"])
    
    logger.info("🟢 Bot started")
    
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        scheduler_task.cancel()
        post_scheduler_task.cancel()
        auto_backup_task.cancel()
        await scheduler.stop()
        await post_scheduler.stop()
        await auto_backup.stop()
        await poster.stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("🔴 Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)