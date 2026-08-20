import asyncio
import logging
import random
import re
import time

from aiogram.fsm.context import FSMContext
from quickchart import QuickChart
import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from handlers.vip import leaderboard_cmd
from loader import db, bot, ADMIN_ID
from keyboards import build_vip_kb, build_main_kb
from utils import safe_send
from services.finder import getsession, getinfo

router = Router()
logger = logging.getLogger(__name__)


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
        "<i>(Якщо не вказати дні, за замовчуванням 7)</i>\n"
        "• <code>/who user_id</code> — Інфо про юзера та його VIP.\n"
        "• <code>/test_digest [user_id]</code> — Прогнати ранковий дайджест зараз.\n\n"

        "<b>🎁 Управління юзерами:</b>\n"
        "• <code>/gift_vip user_id days</code> — Видати VIP.\n"
        "<i>Приклад: /gift_vip 1076078800 30 (0 = назавжди)</i>\n"
        "• <code>/gift_tokens user_id amount</code> — Нарахувати ШІ-токени.\n"
        "<i>Приклад: /gift_tokens 1076078800 1000</i>\n\n"

        "<b>📢 Маркетинг:</b>\n"
        "• <code>/bc ТЕКСТ</code> — Розсилка по всій базі.\n"
        "<i>Приклад: /bc Всім привіт! Оцінки оновились.</i>\n"
        "• <code>/bc_guest ТЕКСТ</code> — Розсилка по ан-логін базі.\n"
        "<i>Приклад: /bc_guest Всім привіт! Увійди в аккаунт.</i>\n"
        "<code>/tiktok</code> — Режим тікток ( скриті посилання )\n"
        "• <code>/pick_winner</code> — Обрати рандомного переможця серед VIP (для розіграшів).\n\n"

        "<i>Натисни на команду, щоб скопіювати її в буфер.</i>"
    )
    await message.answer(text, parse_mode="HTML")


TIKTOK_MODE = False


@router.message(Command("tiktok"))
async def toggle_tiktok(message: Message):
    # Проверка на админа
    if message.from_user.id != ADMIN_ID:
        return

    global TIKTOK_MODE
    TIKTOK_MODE = not TIKTOK_MODE

    status = "🟢 ON (Ссылки скрыты)" if TIKTOK_MODE else "🔴 OFF (Ссылки открыты)"
    await message.answer(f"🎬 <b>TikTok Mode:</b> {status}", parse_mode="HTML")


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
    await message.answer(f"✅ Видано ШІ-токенів: <b>{tokens:,}</b>", parse_mode="HTML")

    try:
        await bot.send_message(
            reply_id,
            f"🎉 Ви отримали ШІ-токени: <b>{tokens:,}</b>\n"
            f"💎 Ваш баланс: <b>{new:,}/1,000,000</b>",
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


@router.message(Command("test_digest"))
async def test_digest(message: Message):
    """Прогнати ранковий дайджест зараз для себе або вказаного user_id.
    Потрібно, бо о 7:30 живої перевірки не буде."""
    if message.from_user.id != ADMIN_ID:
        return

    from services.background import send_digest_to

    parts = message.text.split()
    target = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else message.from_user.id

    login, enc_password, provider = db.get_user(target)
    if not login or not enc_password:
        await message.answer(f"❌ У {target} немає збережених кредів — дайджест неможливий.")
        return

    vip_flag, expires = db.get_vip_status(target)
    is_vip = bool(vip_flag) and (expires == 0 or expires > time.time())

    await message.answer(
        f"🧪 Прогоняю дайджест для <code>{target}</code> (provider={provider}, vip={is_vip})…",
        parse_mode="HTML"
    )
    sent = await send_digest_to(target, login, enc_password, provider, is_vip)
    await message.answer(
        "✅ Дайджест надіслано." if sent
        else "⚪️ Не надіслано: на сьогодні немає уроків або скрап не дав розкладу.\n"
             "<i>Це нормальна поведінка для вихідних/канікул.</i>",
        parse_mode="HTML"
    )


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

        ok = await safe_send(
            uid, text_to_send, photo=photo_id,
            parse_mode="HTML", reply_markup=kb
        )
        if ok:
            count += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)  # Швидка пауза

    await message.answer(f"✅ Розсилка завершена!\n📨 Успішно: {count}\n❌ Не вдалося: {failed}")


@router.message(Command("bc_guest"))
async def broadcast_nologin(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    # 1. Перевірка контенту (текст або підпис до фото)
    content = message.text or message.caption

    if not content:
        await message.answer("⚠️ Повідомлення не містить тексту або підпису.")
        return

    args = content.split(" ", 1)
    if len(args) < 2:
        await message.answer(
            "⚠️ Використання: <code>/bc_guest Текст</code> (можна з фото)\nЦе розсилка ТІЛЬКИ для тих, хто не увійшов.",
            parse_mode="HTML")
        return

    text_to_send = args[1]
    photo_id = message.photo[-1].file_id if message.photo else None

    # Отримуємо список незалогінених
    users = db.get_non_logged_users()
    total_users = len(users)

    if total_users == 0:
        await message.answer("🤷‍♂️ Немає користувачів без логіну.")
        return

    await message.answer(
        f"🚀 <b>Розсилка для «Гостей» ({total_users} чол.) почалась...</b>\nТип: {'Фото 📸' if photo_id else 'Текст 📝'}")

    count, failed = 0, 0

    for (uid,) in users:
        # Для незалогінених ми завжди показуємо звичайну клавіатуру (build_main_kb),
        # бо VIP без логіну навряд чи можливий/корисний.
        ok = await safe_send(
            uid, text_to_send, photo=photo_id,
            parse_mode="HTML", reply_markup=build_main_kb()
        )
        if ok:
            count += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)  # Пауза, щоб не спамити API

    await message.answer(f"✅ Розсилка по незалогінених завершена!\n📨 Успішно: {count}\n❌ Не вдалося: {failed}")


@router.message(Command("bc_stars"))
async def broadcast_stars_smart(message: Message):
    # Перевірка адміна
    if message.from_user.id != ADMIN_ID:
        return

    # --- ВАРІАНТ 1: ДЛЯ ЗВИЧАЙНИХ (ПРОДАЮЧИЙ) ---
    text_no_vip = (
        "⭐️ <b>РОЗІГРАШ 100 ЗІРОК</b>\n\n"
        "Вже в цей понеділок (26.01) ми визначимо переможця.\n\n"
        "⚠️ <b>Важливе нагадування:</b>\n"
        "У розіграші беруть участь <b>ТІЛЬКИ</b> користувачі з VIP-підпискою.\n"
        "Якщо у тебе немає VIP — ти просто глядач.\n\n"
        "🎫 <b>Як збільшити шанси?</b>\n"
        "Твій рейтинг у /leaderboard = твої додаткові квитки.\n"
        "<i>Вхідний квиток у гру коштує копійки 👇</i>"
    )
    kb_no_vip = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 КУПИТИ VIP (ВЗЯТИ УЧАСТЬ)", callback_data="vip_menu")]
    ])

    # --- ВАРІАНТ 2: ДЛЯ VIP (МОТИВУЮЧИЙ) ---
    text_is_vip = (
        "⭐️ <b>РОЗІГРАШ 100 ЗІРОК</b> ✅\n\n"
        "Вже в цей понеділок (26.01) ми визначимо переможця.\n"
        "Так як ви маєте <b>VIP-статус</b> — ви автоматично берете участь! 🥳\n\n"
        "🎫 <b>Хочеш більше шансів на перемогу?</b>\n"
        "Твій рейтинг у /leaderboard = твої додаткові квитки.\n"
        "Запрошуй друзів: <b>1 друг = +1 лотерейний квиток.</b>"
    )
    # Кнопка веде на рефералку або топ, щоб він почав інвайтити
    kb_is_vip = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Піднятися в топі", callback_data="leaderboard")]
    ])

    await message.answer("🚀 <b>Запуск сегментованої розсилки...</b>")

    users = db.get_all_users()
    count_vip = 0
    count_novip = 0
    failed = 0

    for (uid,) in users:
        # 1. Перевіряємо статус (Твій код)
        vip_flag, expires = db.get_vip_status(uid)
        now_ts = int(time.time())
        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

        # 2. Відправляємо відповідний текст
        if is_vip:
            ok = await safe_send(uid, text_is_vip, parse_mode="HTML", reply_markup=kb_is_vip)
            count_vip += 1 if ok else 0
        else:
            ok = await safe_send(uid, text_no_vip, parse_mode="HTML", reply_markup=kb_no_vip)
            count_novip += 1 if ok else 0

        if not ok:
            failed += 1
        await asyncio.sleep(0.05)  # Анти-спам

    await message.answer(
        f"✅ <b>Розсилка завершена!</b>\n\n"
        f"💎 VIP отримали: {count_vip}\n"
        f"👤 Звичайні отримали: {count_novip}\n"
        f"❌ Не дійшло: {failed}"
    )


@router.message(Command("msg"))
async def admin_msg(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    try:
        args = message.text.split(" ", 2)
        receiver_id = args[1]
        msg = args[2]

        await bot.send_message(receiver_id, text=f"📩 Повідомлення від адміна:\n{msg}", parse_mode="HTML")
        await message.reply(f"✅ Надіслано повідомлення юзеру {receiver_id}\n"
                            f"Текст:\n{msg}")
    except Exception as e:
        await message.reply(f"Помилка: {e}")


@router.message(Command("who"))
async def who_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # Тільки для адміна

    args = message.text.split(" ", 1)
    if len(args) < 2 or not args[1].strip():
        await message.answer("Формат: <code>/who user_id</code>", parse_mode="HTML")
        return
    who_id = args[1].strip()

    try:
        # Отримуємо дані переможця
        chat = await message.bot.get_chat(who_id)
        username = f"@{chat.username}" if chat.username else "немає юзернейму"
        name = chat.first_name or "Користувач"
    except Exception:
        username = "невідомо"
        name = "Користувач"

    try:
        vip_flag, expires = db.get_vip_status(int(who_id))
        if not vip_flag:
            vip_line = "немає"
        elif expires == 0:
            vip_line = "назавжди ♾️"
        else:
            until = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y %H:%M")
            active = "активний" if expires > time.time() else "прострочений"
            vip_line = f"{active} до {until} ({db.get_vip_source(int(who_id))})"
    except Exception:
        vip_line = "невідомо"

    # 4. Формуємо повідомлення для адміна з посиланням на профіль
    text = (
        f"🎉 <b>Інформація про {who_id}!</b>\n\n"
        f"👤 <b>Ім'я:</b> {name}\n"
        f"🆔 <b>ID:</b> <code>{who_id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n"
        f"🎟 <b>Vip:</b> {vip_line}\n"
        f"👉 <a href='tg://user?id={who_id}'>ВІДКРИТИ ПРОФІЛЬ ТА ПОДАРУВАТИ</a>\n\n"
    )

    await message.answer(text, parse_mode="HTML")


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
    command_metrics = db.get_command_metrics(days=days, limit=8)
    nz_metrics = db.get_nz_session_metrics(days=days)

    nz_verified = db.count_verified_by_provider("nz")
    human_verified = db.count_verified_by_provider("human")

    # 3. Генерація графіків (виклики функцій)
    growth_url = _get_growth_chart(growth_labels, growth_data)
    activity_url = _get_activity_chart(stats_data, days)

    # 4. Формування тексту
    # Лайфхак: використовуємо sum(growth_data) для тексту, щоб гарантувати збіг з графіком
    command_lines = []
    for item in command_metrics:
        command_lines.append(
            f"├ {item['command']}: {item['calls']}x, avg {item['avg_ms']}ms, err {item['errors']}"
        )
    if command_lines:
        command_lines[-1] = command_lines[-1].replace("├", "└", 1)
    else:
        command_lines.append("└ ще немає даних")

    cookie_reuse = nz_metrics.get("cookie_reuse", 0)
    memory_hit = nz_metrics.get("memory_hit", 0)
    memory_miss = nz_metrics.get("memory_miss", 0)
    login_count = nz_metrics.get("login", 0)
    expired_count = nz_metrics.get("cookie_expired", 0)
    memory_total = memory_hit + memory_miss
    memory_rate = round(memory_hit * 100 / memory_total, 1) if memory_total else 0

    text = (
        f"📊 <b>Аналітика Database</b>\n"
        f"📅 Період: {days} днів\n\n"
        f"👥 <b>Всього юзерів:</b> {stats_data['total']}\n"
        f"├ 🔐 З даними: {stats_data['total_creds']}\n"
        f"├ 🚫 Заблокували бота: {db.count_blocked()}\n"
        f"├ ✅ NZ Valid: {nz_verified}\n"
        f"└ ✅ Human Valid: {human_verified}\n\n"
        f"📈 <b>Ріст:</b>\n"
        f"├ 🆕 Сьогодні: +{stats_data['new_today']}\n"
        f"└ 🆕 За {days} днів: +{sum(growth_data)}\n\n"
        f"⚡ <b>NZ сесії:</b>\n"
        f"├ reuse cookies: {cookie_reuse}\n"
        f"├ login: {login_count}\n"
        f"├ expired: {expired_count}\n"
        f"└ memory hit: {memory_rate}% ({memory_hit}/{memory_total})\n\n"
        f"🧭 <b>Топ дій:</b>\n"
        f"{chr(10).join(command_lines)}"
    )

    # 5. Відправка
    media = [
        InputMediaPhoto(media=growth_url, caption=text, parse_mode="HTML"),
        InputMediaPhoto(media=activity_url)
    ]
    await message.answer_media_group(media)


# --- NaurokFinder: особиста фіча, ТІЛЬКИ для адміна ---
# Працює через NAUROK_COOKIE власника, тому доступ сторонніх виключений.
@router.message(F.text.startswith("https://naurok.com.ua/test"))
async def naurok_finder(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    url = message.text
    if url.startswith('https://naurok.com.ua/test/testing/'):
        try:
            url = re.sub(r'/test/testing/', '/test/realtime-client/', url)
        except Exception:
            logger.exception("Failed to normalize Naurok testing url")
    data = getsession(url)
    info = getinfo(data)
    await message.answer(f"Назва тесту: <i>{info}</i>", parse_mode="HTML")
