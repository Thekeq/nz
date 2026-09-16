import asyncio
import datetime
import os
import time
import re
import logging
from html import escape
import requests
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loader import (db, SEMAPHORE, SENT_REMINDERS, WRAPPED_CACHE, HW_AI_CACHE,
    USER_LAST_CALL, KYIV_TZ, fernet,
    COOKIE_API_URL, COOKIE_API_TOKEN, COOKIE_SOURCE, COOKIE_VIP_DAYS
)
from services.diarynz import (cleanup_session_cache, get_diary_schedule, get_grade_events,
    get_diary_homework, get_homework_events
)
from services.digest import has_lessons, has_conf_link, build_digest_text, is_school_time
from utils import safe_send
import gc

logger = logging.getLogger(__name__)

LESSON_TIMES = ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]
LEAD_MIN = 5
DIGEST_HOUR = 7
DIGEST_MINUTE = 30
DIGEST_ACTIVE_DAYS = 14  # кого вважаємо живим і варто скрапити щоранку

IDLE_SLEEP_SEC = 5 * 60

BACKUP_DIR = "backups"
BACKUP_KEEP = 7


async def sleep_to_next_minute():
    """Чекає до початку наступної хвилини (щоб запускатися рівно в :00 секунд)."""
    now = datetime.datetime.now(tz=KYIV_TZ)
    nxt = (now + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    await asyncio.sleep((nxt - now).total_seconds())


def build_dt_for_today(hhmm: str, now: datetime.datetime) -> datetime.datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


async def check_lessons():
    while True:
        # Поза шкільним часом навіть не ходимо в БД: раніше цикл щохвилини
        # будив усіх користувачів цілодобово, включно з канікулами й ніччю
        if not is_school_time(datetime.datetime.now(KYIV_TZ)):
            await asyncio.sleep(IDLE_SLEEP_SEC)
            continue

        users = db.get_users_with_notify()
        now_ts = int(time.time())

        tasks = []
        for user_id, login, enc_password in users:
            try:
                vip_flag, expires = db.get_vip_status(user_id)
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if not login or not enc_password or not is_vip:
                    continue
                tasks.append(_check_lessons_nz(user_id, login, enc_password))
            except Exception:
                logger.exception("Failed to schedule lesson check for user_id=%s", user_id)

        if tasks:
            # паралельно, щоб усі юзери встигали у вікно нагадування;
            # навантаження на скрапінг обмежує SEMAPHORE усередині задач
            await asyncio.gather(*tasks)

        await sleep_to_next_minute()


async def _check_lessons_nz(user_id: int, login: str, enc_password: str):
    """NZ: скрапимо тільки коли якийсь урок у вікні нагадування.
    Скрап саме в момент перевірки (а не з ранкового кешу) — щоб побачити
    посилання, яке вчитель додав за 10 хвилин до уроку."""
    try:
        now = datetime.datetime.now(KYIV_TZ)

        target_idx = target_time = target_key = None
        for idx, lesson_time in enumerate(LESSON_TIMES, start=1):
            lesson_dt = build_dt_for_today(lesson_time, now)
            notify_dt = lesson_dt - datetime.timedelta(minutes=LEAD_MIN)
            # вікно аж до початку уроку: якщо тік запізнився, нагадування
            # все одно піде, а дублі відсікає SENT_REMINDERS
            if notify_dt <= now < lesson_dt:
                key = f"nz|{now.date().isoformat()}|{lesson_time}"
                if (user_id, key) not in SENT_REMINDERS:
                    target_idx, target_time, target_key = idx, lesson_time, key
                break

        if target_idx is None:
            return

        password = fernet.decrypt(enc_password.encode()).decode()
        async with SEMAPHORE:
            schedule_text = await asyncio.to_thread(get_diary_schedule,
                login,
                password, days=["сьогодні"], user_id=user_id, db=db, fernet=fernet
            )

        lessons_list = []
        for line in schedule_text.splitlines():
            m = re.match(r"\d+\.\s*(.*)", line)
            if m:
                lessons_list.append(m.group(1).strip())

        if len(lessons_list) < target_idx:
            return

        lesson_name = lessons_list[target_idx - 1]
        if not has_conf_link(lesson_name):
            return  # посилання ще нема — перевіримо наступної хвилини

        text = (
            f"🔔 Нагадування: через {LEAD_MIN} хвилин починається "
            f"<b>{lesson_name}</b> о <b>{target_time}</b>."
        )
        if await safe_send(user_id, text, parse_mode="HTML", disable_web_page_preview=False):
            SENT_REMINDERS.add((user_id, target_key))
    except Exception:
        logger.exception("NZ lesson reminder failed for user_id=%s", user_id)


async def check_grades():
    while True:
        users = db.get_users_with_grades_notify()  # (user_id, login, enc_password)
        for user_id, login, enc_password in users:
            try:
                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if not is_vip or not login or not enc_password:
                    continue

                password = fernet.decrypt(enc_password.encode()).decode()

                async with SEMAPHORE:
                    events = await asyncio.to_thread(get_grade_events,
                        login,
                        password,
                        20, user_id=user_id, db=db, fernet=fernet
                    )

                if not events:
                    continue

                last = set(db.get_last_grade_hashes(user_id))

                # первый запуск: запоминаем самый свежий и НЕ шлём историю
                if not last:
                    db.set_last_grade_hashes(user_id, [e["hash"] for e in events[:3]])
                    continue

                new_events = []
                for ev in events:
                    if ev["hash"] in last:
                        break
                    new_events.append(ev)

                if new_events:
                    # отправим в нормальном виде (без простыни)
                    lines = ["🆕 <b>Нові оцінки:</b>"]
                    sent = new_events[:10]
                    for ev in sent:
                        lines.append(f"• <b>{ev['name']}</b>:\n{ev['text']}")

                    # хеши фиксируем только после успешной отправки:
                    # если Telegram не принял — попробуем в следующем цикле
                    if await safe_send(user_id, "\n\n".join(lines), disable_web_page_preview=True):
                        db.set_last_grade_hashes(user_id, [e["hash"] for e in events[:3]])
                    await asyncio.sleep(0.25)

            except Exception:
                logger.exception("Grade notification check failed for user_id=%s", user_id)

        await asyncio.sleep(60 * 10)


async def memory_cleaner_task():
    """Фонова задача для очистки оперативної пам'яті та застарілих записів."""
    while True:
        await asyncio.sleep(3600)  # Запускаем раз в 1 час (3600 сек)

        # 1. Кеші "тут і зараз": якщо юзер захоче через годину — згенерує заново
        WRAPPED_CACHE.clear()
        HW_AI_CACHE.clear()
        cleanup_session_cache()

        now = datetime.datetime.now(KYIV_TZ)
        today = now.date().isoformat()

        # 2. SENT_REMINDERS: ключі формату "nz|YYYY-MM-DD|..." —
        # прибираємо все, що не за сьогодні
        stale = {
            item for item in SENT_REMINDERS
            if len(item[1].split("|")) < 2 or item[1].split("|")[1] != today
        }
        SENT_REMINDERS.difference_update(stale)

        # 3. Рейт-ліміти, старші за годину (інакше словник росте безмежно)
        cutoff = time.time() - 3600
        for key in [k for k, ts in USER_LAST_CALL.items() if ts < cutoff]:
            USER_LAST_CALL.pop(key, None)

        # 4. Завислі FSM-стани (юзер кинув авторизацію і пішов)
        try:
            db.fsm_purge_older_than(48 * 3600)
        except Exception:
            logger.exception("FSM purge failed")

        # 5. Примусовий збір сміття — прибирає «висячі» об'єкти
        # картинок і буферів, важливо на VPS з малим обсягом RAM
        gc.collect()


async def _fetch_digest_parts(user_id: int, login: str, enc_password: str):
    """Тягне розклад і ДЗ на сьогодні. Повертає (schedule, homework)."""
    password = fernet.decrypt(enc_password.encode()).decode()

    async with SEMAPHORE:
        schedule = await asyncio.to_thread(
            get_diary_schedule, login, password, days=["сьогодні"], user_id=user_id, db=db, fernet=fernet
        )

    if not has_lessons(schedule):
        return schedule, ""

    homework = ""
    try:
        async with SEMAPHORE:
            homework = await asyncio.to_thread(
                get_diary_homework, login, password, days=["сьогодні"], user_id=user_id, db=db, fernet=fernet
            )
    except Exception:
        # розклад важливіший за ДЗ — без нього дайджест все одно корисний
        logger.exception("Digest homework fetch failed for user_id=%s", user_id)

    return schedule, homework


async def send_digest_to(user_id: int, login: str, enc_password: str, is_vip: bool) -> bool:
    """True якщо дайджест надіслано. Порожній день — не надсилаємо нічого."""
    try:
        schedule, homework = await _fetch_digest_parts(user_id, login, enc_password)
        if not has_lessons(schedule):
            return False

        kb = None if is_vip else InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Хочу щоранку", callback_data="vip_menu")]
        ])

        return await safe_send(user_id,
            build_digest_text(schedule, homework, is_vip), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb
        )
    except Exception:
        logger.exception("Digest failed for user_id=%s", user_id)
        return False


async def morning_digest_task():
    """07:30 — дайджест дня. VIP щодня, решта по понеділках (з апселом)."""
    while True:
        now = datetime.datetime.now(KYIV_TZ)
        nxt = now.replace(hour=DIGEST_HOUR, minute=DIGEST_MINUTE, second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())

        try:
            today = datetime.datetime.now(KYIV_TZ)
            if today.weekday() >= 5:
                continue  # вихідні — уроків нема, не скрапимо взагалі

            is_monday = today.weekday() == 0
            recipients = [
                row for row in db.get_digest_recipients(DIGEST_ACTIVE_DAYS)
                if is_monday or row[3]  # row[3] = is_vip
            ]
            logger.info("Morning digest: %s recipients (monday=%s)", len(recipients), is_monday)

            sent = 0
            # чанками, щоб не тримати тисячу корутин на low-RAM VPS
            for i in range(0, len(recipients), 25):
                chunk = recipients[i:i + 25]
                results = await asyncio.gather(*[
                    send_digest_to(uid, login, pwd, is_vip)
                    for uid, login, pwd, is_vip in chunk
                ])
                sent += sum(1 for ok in results if ok)
                await asyncio.sleep(1)

            logger.info("Morning digest done: sent=%s", sent)
        except Exception:
            logger.exception("Morning digest task failed")


async def check_homework():
    """Пуш про нове ДЗ з Нових Знань."""
    while True:
        # ДЗ задають протягом навчального дня — вночі й на вихідних не скрапимо
        if not is_school_time(datetime.datetime.now(KYIV_TZ)):
            await asyncio.sleep(IDLE_SLEEP_SEC)
            continue

        for user_id, login, enc_password in db.get_users_with_homework_notify():
            try:
                if not login or not enc_password:
                    continue

                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                if not (bool(vip_flag) and (expires == 0 or expires > now_ts)):
                    continue

                password = fernet.decrypt(enc_password.encode()).decode()
                async with SEMAPHORE:
                    events = await asyncio.to_thread(get_homework_events, login, password, days=["сьогодні", "завтра"], user_id=user_id, db=db, fernet=fernet
                    )

                if not events:
                    continue

                known = db.get_homework_hashes(user_id)
                current = [e["hash"] for e in events]

                # перший запуск: запам'ятовуємо стан і НЕ шлемо всю історію
                if not known:
                    db.set_homework_hashes(user_id, current)
                    continue

                known_set = set(known)
                new_events = [e for e in events if e["hash"] not in known_set]
                if not new_events:
                    continue

                lines = ["📕 <b>Нове домашнє завдання</b>", ""]
                for ev in new_events[:5]:
                    lines.append(f"• <b>{escape(ev['subject'])}</b> ({escape(ev['day'])}):")
                    lines.append(f"<blockquote expandable>{escape(ev['hw'])}</blockquote>")
                if len(new_events) > 5:
                    lines.append(f"\n<i>…і ще {len(new_events) - 5}</i> — /homework")

                # хеші фіксуємо лише після успішної відправки
                if await safe_send(user_id, "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True):
                    merged = current + [h for h in known if h not in set(current)]
                    db.set_homework_hashes(user_id, merged)
                await asyncio.sleep(0.25)

            except Exception:
                logger.exception("Homework notification failed for user_id=%s", user_id)

        await asyncio.sleep(20 * 60)


WINBACK_GRACE_SEC = 48 * 3600  # тримати синхронно з handlers/vip.py


async def vip_expiry_task():
    """Воронка закінчення VIP: нагадування за ~добу до кінця
    і win-back знижка протягом 48 годин після."""
    while True:
        try:
            for user_id, expires in db.get_vips_expiring_within(24 * 3600):
                if await safe_send(user_id,
                    "⏳ <b>Твій VIP закінчується завтра!</b>\n"
                    "Після цього вимкнуться ⏰ нагадування перед уроками "
                    "і 🔔 сповіщення про оцінки.\n\n"
                    "Продовжити: /vip", parse_mode="HTML"
                ):
                    db.set_expiry_stage(user_id, 1)
                await asyncio.sleep(0.25)

            winback_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Місяць за 50 ⭐️ (-33%)", callback_data="buy_winback")]
            ])
            for user_id, expires in db.get_vips_just_expired(WINBACK_GRACE_SEC):
                if await safe_send(user_id,
                    "😔 <b>VIP закінчився</b> — нагадування і сповіщення вимкнено.\n\n"
                    "🎁 Тільки <b>48 годин</b>: місяць VIP за <b>50 ⭐️ замість 75</b>", parse_mode="HTML", reply_markup=winback_kb
                ):
                    db.set_expiry_stage(user_id, 2)
                    db.record_command_metric("funnel:winback_sent", 0)
                await asyncio.sleep(0.25)
        except Exception:
            logger.exception("VIP expiry check failed")

        await asyncio.sleep(3600)


COOKIE_POLL_SEC = 10 * 60


def _cookie_source_users() -> list[int]:
    """Хто зареєструвався у Cookie Merge за нашим посиланням. Синхронно —
    викликається через to_thread, як і решта мережевих походів тут."""
    resp = requests.get(
        f"{COOKIE_API_URL}/internal/source/{COOKIE_SOURCE}",
        headers={"Authorization": f"Bearer {COOKIE_API_TOKEN}"},
        timeout=10,
    )
    resp.raise_for_status()
    return [int(u) for u in resp.json().get("users", [])]


async def partner_vip_task():
    """Тиждень VIP тим, хто пішов у Cookie Merge за нашим посиланням.

    Гра віддає ВЕСЬ список своїх реєстрацій із міткою src_nz, а хто вже
    отримав нагороду — пам'ятаємо ми: платить рівно вставка в partner_grants.
    Тому опитування безпечно повторювати, а задача, що впала посеред видачі,
    доплатить решту наступним проходом.

    VIP видаємо навіть тому, кого бот ще не бачив: set_vip заводить рядок
    сам, і якщо людина натисне /start пізніше — підписка вже чекає. Тому
    невдала відправка повідомлення нагороду не скасовує."""
    if not COOKIE_API_TOKEN:
        logger.info("Cookie partner sync off: PARTNER_TOKEN not set")
        return
    while True:
        try:
            for user_id in await asyncio.to_thread(_cookie_source_users):
                if not db.claim_partner_grant(user_id, COOKIE_SOURCE):
                    continue
                db.set_vip(user_id, days=COOKIE_VIP_DAYS, source="partner")
                db.record_command_metric("partner:cookie_vip", 0)
                await safe_send(user_id,
                    f"🎁 <b>{COOKIE_VIP_DAYS} днів VIP — за Cookie Merge!</b>\n\n"
                    "Ти зайшов у гру за нашим посиланням, і VIP уже активний:\n"
                    "⏰ нагадування перед уроками\n"
                    "🔔 сповіщення про оцінки\n"
                    "📬 ранковий дайджест\n\n"
                    "Термін і статус: /vip", parse_mode="HTML"
                )
                await asyncio.sleep(0.25)
        except Exception:
            logger.exception("Cookie partner sync failed")

        await asyncio.sleep(COOKIE_POLL_SEC)


async def daily_backup_task():
    """Щоденний бекап бази о ~03:30: там платні підписки, втрачати не можна."""
    while True:
        now = datetime.datetime.now(KYIV_TZ)
        nxt = now.replace(hour=3, minute=30, second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())

        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            dest = os.path.join(BACKUP_DIR, f"data-{datetime.date.today().isoformat()}.db")
            await asyncio.to_thread(db.backup_to, dest)

            files = sorted(
                f for f in os.listdir(BACKUP_DIR)
                if f.startswith("data-") and f.endswith(".db")
            )
            for old in files[:-BACKUP_KEEP]:
                os.remove(os.path.join(BACKUP_DIR, old))
            logger.info("DB backup created: %s", dest)
        except Exception:
            logger.exception("DB backup failed")
