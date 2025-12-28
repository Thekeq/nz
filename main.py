import asyncio
import logging
from loader import dp, bot
from handlers import auth, school, vip, admin, common
from services.background import check_lessons, check_grades, memory_cleaner_task

# Налаштування логування
logging.basicConfig(level=logging.WARNING)


async def main():
    print("🚀 Bot starting...")

    # Реєстрація роутерів
    dp.include_router(admin.router)
    dp.include_router(auth.router)
    dp.include_router(vip.router)
    dp.include_router(school.router)
    dp.include_router(common.router)

    # Запуск фонових задач
    asyncio.create_task(check_lessons())
    asyncio.create_task(check_grades())
    asyncio.create_task(memory_cleaner_task())

    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
