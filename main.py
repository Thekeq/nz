import asyncio
import logging
from html import escape
from loader import dp, bot, LOG_LEVEL, db, ADMIN_ID
from handlers import auth, school, vip, admin, common
from middlewares import MetricsMiddleware
from services.background import (
    check_lessons, check_grades, check_homework, memory_cleaner_task,
    daily_backup_task, vip_expiry_task, morning_digest_task,
    partner_vip_task,
)

# Налаштування логування
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def supervised(name: str, task_fn):
    """Тримає фонову задачу живою: якщо впала — алерт адміну і перезапуск.
    Без цього виняток поза per-user try/except назавжди вбиває задачу."""
    while True:
        try:
            await task_fn()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Background task %s crashed, restarting in 60s", name)
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Фонова задача <b>{name}</b> впала:\n"
                    f"<code>{escape(repr(e))}</code>\n"
                    f"Перезапуск через 60 сек."
                )
            except Exception:
                pass
            await asyncio.sleep(60)


async def main():
    logger.info("Bot starting")

    metrics = MetricsMiddleware(db)
    dp.message.middleware(metrics)
    dp.callback_query.middleware(metrics)

    # Реєстрація роутерів
    dp.include_router(admin.router)
    dp.include_router(auth.router)
    dp.include_router(vip.router)
    dp.include_router(school.router)
    dp.include_router(common.router)

    # Запуск фонових задач (із перезапуском при падінні)
    asyncio.create_task(supervised("check_lessons", check_lessons))
    asyncio.create_task(supervised("check_grades", check_grades))
    asyncio.create_task(supervised("check_homework", check_homework))
    asyncio.create_task(supervised("morning_digest", morning_digest_task))
    asyncio.create_task(supervised("memory_cleaner", memory_cleaner_task))
    asyncio.create_task(supervised("daily_backup", daily_backup_task))
    asyncio.create_task(supervised("vip_expiry", vip_expiry_task))
    asyncio.create_task(supervised("partner_vip", partner_vip_task))

    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
