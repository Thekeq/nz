import asyncio
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from loader import db, bot, ADMIN_ID
from keyboards import build_vip_kb
import datetime

router = Router()


@router.message(Command("gift_vip"))
async def gift_vip(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    # Перевірка аргументів
    if len(message.text.split()) != 3:
        await message.answer("Формат: /gift_vip <user_id> <days>\n(Введи 0 днів для VIP назавжди)")
        return

    try:
        _, target_id_str, days_str = message.text.split()
        reply_id = int(target_id_str)
        days = int(days_str)
    except ValueError:
        await message.answer("❌ ID та дні мають бути числами.")
        return

    # Викликаємо оновлену функцію БД
    db.set_vip(reply_id, days=days)

    # Отримуємо новий статус для підтвердження
    vip, expires = db.get_vip_status(reply_id)

    # Формуємо красивий текст
    if expires == 0:
        date_str = "Назавжди ♾️"
    else:
        date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y %H:%M")

    await message.answer(f"✅ Видано VIP доступ: <b>{date_str}</b>", parse_mode="HTML")

    try:
        await bot.send_message(
            reply_id,
            f"🎉 Ви отримали VIP доступ: <b>{date_str}</b>",
            reply_markup=build_vip_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("⚠️ Юзеру видано, але повідомлення не надіслано (можливо, бот заблокований).")


@router.message(Command("bc"))
async def broadcast(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("⚠️ Використання: /bc <текст повідомлення>")
        return

    text = args[1]

    users = db.get_all_users()
    count, failed = 0, 0

    for (uid,) in users:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.15)

        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            # повторим один раз
            try:
                await bot.send_message(uid, text, parse_mode="HTML")
                count += 1
                await asyncio.sleep(0.15)
            except Exception:
                failed += 1

        except TelegramForbiddenError:
            # юзер заблокал бота / запретил сообщения
            failed += 1

        except Exception:
            failed += 1

    await message.answer(f"✅ Розсилка завершена!\n📨 Успішно: {count}\n❌ Не вдалося: {failed}")


@router.message(Command("stat"))
async def stats(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    parts = message.text.split()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 7
    stats = db.get_activity_summary(days=days)

    nz_verified = db.count_verified_by_provider("nz")
    human_verified = db.count_verified_by_provider("human")

    text = (
        f"📊 Аналітика database:\n\n"
        f"👥 Всього юзерів: {stats['total']}\n"
        f"🔐 З NZ-акаунтом: {stats['total_creds']}\n"
        f"✅ Підтверджені NZ: {nz_verified}\n"
        f"✅ Підтверджені Human: {human_verified}\n"
        f"🆕 Нових сьогодні: {stats['new_today']}\n"
        f"🆕 Нових за {days}-днів: {stats['new_days']}\n\n"
        f"📊 Статистика активності за останні {days} днів:\n\n"
        f"💤 Неактивні (0 дій): {stats['inactive']}\n"
        f"😴 Слабо активні (1–2): {stats['low_active']}\n"
        f"🔥 Активні (3–6): {stats['active']}\n"
        f"⚡ Дуже активні (7+): {stats['very_active']}\n"
        f"💎 Цінні (7+ та VIP): {stats['valuable']}\n"
    )

    await message.answer(text, parse_mode="HTML")
