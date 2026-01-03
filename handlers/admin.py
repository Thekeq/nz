import asyncio
import random
import time
from quickchart import QuickChart
import datetime

from aiogram import Router
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto
from loader import db, bot, ADMIN_ID
from keyboards import build_vip_kb, build_main_kb

router = Router()


# --- 👮‍♂️ ГОЛОВНЕ МЕНЮ АДМІНА ---
@router.message(Command("admin"))
async def admin_help(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    text = (
        "👮‍♂️ <b>Адмін панель</b>\n\n"

        "<b>📊 Аналітика та Графіки:</b>\n"
        "• <code>/stat days</code> — Показати статистику та графіки за N днів (макс. 90).\n"
        "<i>(Якщо не вказати дні, за замовчуванням 7)</i>\n\n"

        "<b>🎁 Управління юзерами:</b>\n"
        "• <code>/gift_vip user_id days</code> — Видати VIP.\n"
        "<i>Приклад: /gift_vip 1076078800 30 (0 = назавжди)</i>\n"
        "• <code>/gift_tokens user_id amount</code> — Нарахувати AI токени.\n"
        "<i>Приклад: /gift_tokens 1076078800 1000</i>\n\n"

        "<b>📢 Маркетинг:</b>\n"
        "• <code>/bc ТЕКСТ</code> — Розсилка по всій базі.\n"
        "<i>Приклад: /bc Всім привіт! Оцінки оновились.</i>\n"
        "• <code>/pick_winner</code> — Обрати рандомного переможця серед VIP (для розіграшів).\n\n"

        "<i>Натисни на команду, щоб скопіювати її в буфер.</i>"
    )
    await message.answer(text, parse_mode="HTML")


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


@router.message(Command("gift_tokens"))
async def gift_tokens(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    # Перевірка аргументів
    if len(message.text.split()) != 3:
        await message.answer("Формат: /gift_tokens <user_id> <days>\n")
        return

    try:
        _, target_id_str, tokens_str = message.text.split()
        reply_id = int(target_id_str)
        tokens = int(tokens_str)
    except ValueError:
        await message.answer("❌ ID та токени мають бути числами.")
        return

    # Викликаємо оновлену функцію БД
    old = db.get_tokens(reply_id)
    db.set_tokens(reply_id, old + tokens)

    # Отримуємо новий баланс для підтвердження
    new = db.get_tokens(reply_id)

    # Формуємо красивий текст
    await message.answer(f"✅ Видано AI-tokens: <b>{tokens}</b>", parse_mode="HTML")

    try:
        await bot.send_message(
            reply_id,
            f"🎉 Ви отримали AI-tokens: <b>{tokens}</b>\n"
            f"💎 Ваш баланс: <b>{new}</b>",
            parse_mode="HTML"
        )
    except Exception:
        await message.answer("⚠️ Юзеру видано, але повідомлення не надіслано (можливо, бот заблокований).")


@router.message(Command("pick_winner"))
async def pick_winner_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # Тільки для адміна

    # 1. Отримуємо "барабан" із квитками
    drum = db.get_raffle_participants(days=14)

    if not drum:
        await message.answer("❌ Немає учасників для розіграшу (потрібні VIP користувачі).")
        return

    # 2. Рандомно обираємо переможця
    winner_id = random.choice(drum)

    # 3. Рахуємо статистику для звіту
    total_tickets = len(drum)
    winner_tickets = drum.count(winner_id)
    chance = round((winner_tickets / total_tickets) * 100, 2)

    try:
        # Отримуємо дані переможця
        chat = await message.bot.get_chat(winner_id)
        username = f"@{chat.username}" if chat.username else "немає юзернейму"
        name = chat.first_name or "Користувач"
    except Exception:
        username = "невідомо"
        name = "Користувач"

    # 4. Формуємо повідомлення для адміна з посиланням на профіль
    text = (
        f"🎉 <b>ПЕРЕМОЖЕЦЬ ОБРАНИЙ!</b>\n\n"
        f"👤 <b>Ім'я:</b> {name}\n"
        f"🆔 <b>ID:</b> <code>{winner_id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n"
        f"🎟 <b>Квитків у нього:</b> {winner_tickets}\n"
        f"📈 <b>Шанс був:</b> {chance}%\n\n"
        f"👉 <a href='tg://user?id={winner_id}'>ВІДКРИТИ ПРОФІЛЬ ТА ПОДАРУВАТИ</a>\n\n"
        f"<i>Тепер ти можеш переслати це повідомлення в канал або зробити скріншот!</i>"
    )

    await message.answer(text, parse_mode="HTML")


@router.message(Command("bc"))
async def broadcast(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    # 1. Розумна перевірка: це текст чи підпис до фото?
    content = message.text or message.caption

    if not content:
        await message.answer("⚠️ Повідомлення не містить тексту або підпису.")
        return

    args = content.split(" ", 1)
    if len(args) < 2:
        await message.answer("⚠️ Використання: <code>/bc Текст</code> (можна з фото)", parse_mode="HTML")
        return

    text_to_send = args[1]  # Текст, який полетить юзерам (без /bc)

    # Визначаємо, чи є фото
    photo_id = message.photo[-1].file_id if message.photo else None

    await message.answer(f"🚀 <b>Розсилка почалась...</b>\nТип: {'Фото 📸' if photo_id else 'Текст 📝'}")

    users = db.get_all_users()
    count, failed = 0, 0

    for (uid,) in users:
        # Перевіряємо VIP для клавіатури
        vip_flag, expires = db.get_vip_status(uid)
        now_ts = int(time.time())
        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
        kb = build_vip_kb() if is_vip else build_main_kb()

        try:
            if photo_id:
                # Якщо це фото — надсилаємо фото з підписом
                await bot.send_photo(uid, photo=photo_id, caption=text_to_send, parse_mode="HTML", reply_markup=kb)
            else:
                # Якщо просто текст — надсилаємо текст
                await bot.send_message(uid, text=text_to_send, parse_mode="HTML", reply_markup=kb)

            count += 1
            await asyncio.sleep(0.05)  # Швидка пауза

        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                # Повторна спроба
                if photo_id:
                    await bot.send_photo(uid, photo=photo_id, caption=text_to_send, parse_mode="HTML", reply_markup=kb)
                else:
                    await bot.send_message(uid, text=text_to_send, parse_mode="HTML", reply_markup=kb)
                count += 1
            except Exception:
                failed += 1

        except TelegramForbiddenError:
            failed += 1  # Юзер заблокував бота

        except Exception as e:
            # print(f"Error sending to {uid}: {e}") # Можна розкоментувати для дебагу
            failed += 1

    await message.answer(f"✅ Розсилка завершена!\n📨 Успішно: {count}\n❌ Не вдалося: {failed}")


# --- ХЕЛПЕРИ (Винеси їх окремо, або залиш над хендлером) ---

def _get_activity_chart(stats, days):
    """Генерує URL бублика активності"""
    qc = QuickChart()
    qc.width = 800
    qc.height = 500
    qc.device_pixel_ratio = 2.0
    qc.background_color = "#1e1e1e"

    chart_labels = [
        f"Неактивні ({stats['inactive']})",
        f"Мало-активні ({stats['low_active']})",
        f"Активні ({stats['active']})",
        f"Дуже-активні ({stats['very_active']})",
        f"Віп ({stats['valuable']})"
    ]
    chart_data = [
        stats['inactive'], stats['low_active'],
        stats['active'], stats['very_active'], stats['valuable']
    ]

    qc.config = {
        "type": "doughnut",
        "data": {
            "labels": chart_labels,
            "datasets": [{
                "data": chart_data,
                "backgroundColor": ["#e74c3c", "#f1c40f", "#3498db", "#2ecc71", "#9b59b6"],
                "borderColor": "#1e1e1e", "borderWidth": 4
            }]
        },
        "options": {
            "plugins": {"datalabels": {"display": False}},
            "legend": {
                "position": "right", "align": "center",
                "labels": {"fontColor": "#fff", "fontSize": 16, "fontStyle": "bold", "padding": 20}
            },
            "title": {
                "display": True, "text": f"Активність за {days} днів",
                "fontColor": "#ecf0f1", "fontSize": 24
            },
            "cutoutPercentage": 70
        }
    }
    return qc.get_url()


def _get_growth_chart(labels, data):
    """Генерує чистий лінійний графік приросту (тільки цифри зверху)"""

    qc = QuickChart()
    qc.width = 800
    qc.height = 500
    qc.device_pixel_ratio = 2.0
    qc.background_color = "#1e1e1e"  # Темний фон

    qc.config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "data": data,
                    # Стиль лінії
                    "borderColor": "#3498db",  # Синій
                    "borderWidth": 3,
                    "backgroundColor": "rgba(52, 152, 219, 0.1)",  # Легка заливка під графіком
                    "fill": True,
                    # Стиль точок
                    "pointBackgroundColor": "#3498db",
                    "pointBorderColor": "#ffffff",
                    "pointBorderWidth": 2,
                    "pointRadius": 5,
                    "pointHoverRadius": 7,
                    # Налаштування підписів (цифри зверху)
                    "datalabels": {
                        "display": True,
                        "align": "top",
                        "anchor": "end",
                        "color": "#ffffff",
                        "font": {
                            "weight": "bold",
                            "size": 14
                        },
                        "offset": 4
                    }
                }
            ]
        },
        "options": {
            "legend": {"display": False},
            "title": {
                "display": True,
                "text": f"Динаміка нових користувачів (+{sum(data)})",
                "fontColor": "#fff", "fontSize": 24, "padding": 20
            },
            "scales": {
                "xAxes": [{
                    "ticks": {"fontColor": "#bdc3c7", "fontSize": 14, "padding": 10},
                    "gridLines": {"display": False}  # Без вертикальної сітки
                }],
                "yAxes": [{
                    "ticks": {
                        "fontColor": "#bdc3c7",
                        "fontSize": 14,
                        "beginAtZero": True,
                        "precision": 0,
                        "padding": 10,
                        # Трохи збільшуємо верхню межу, щоб цифри не обрізало
                        "suggestedMax": max(data) * 1.2 if data and max(data) > 0 else 5
                    },
                    "gridLines": {"display": False}  # Без горизонтальної сітки
                }]
            },
            "layout": {
                "padding": {
                    "top": 20,
                    "bottom": 10,
                    "left": 10,
                    "right": 10
                }
            },
            "plugins": {
                "datalabels": {
                    "display": True  # Вмикаємо плагін
                }
            }
        }
    }
    return qc.get_url()


# --- ОСНОВНИЙ ХЕНДЛЕР ---

@router.message(Command("stat"))
async def stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    # 1. Парсинг аргументів
    parts = message.text.split()
    days_ = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 7
    days = days_ if days_ <= 90 else 7
    # 2. Отримання даних з БД
    stats_data = db.get_activity_summary(days=days)
    growth_labels, growth_data = db.get_daily_growth(days=days)

    nz_verified = db.count_verified_by_provider("nz")
    human_verified = db.count_verified_by_provider("human")

    # 3. Генерація графіків (виклики функцій)
    growth_url = _get_growth_chart(growth_labels, growth_data)
    activity_url = _get_activity_chart(stats_data, days)

    # 4. Формування тексту
    # Лайфхак: використовуємо sum(growth_data) для тексту, щоб гарантувати збіг з графіком
    text = (
        f"📊 <b>Аналітика Database</b>\n"
        f"📅 Період: {days} днів\n\n"
        f"👥 <b>Всього юзерів:</b> {stats_data['total']}\n"
        f"├ 🔐 З даними: {stats_data['total_creds']}\n"
        f"├ ✅ NZ Valid: {nz_verified}\n"
        f"└ ✅ Human Valid: {human_verified}\n\n"
        f"📈 <b>Ріст:</b>\n"
        f"├ 🆕 Сьогодні: +{stats_data['new_today']}\n"
        f"└ 🆕 За {days} днів: +{sum(growth_data)}\n"
    )

    # 5. Відправка
    media = [
        InputMediaPhoto(media=growth_url, caption=text, parse_mode="HTML"),
        InputMediaPhoto(media=activity_url)
    ]
    await message.answer_media_group(media)
