import asyncio
import datetime
import os
import time
import re
import logging
from html import escape
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from loader import (
    db, SEMAPHORE, SENT_REMINDERS, WRAPPED_CACHE, HW_AI_CACHE,
    USER_LAST_CALL, KYIV_TZ, fernet,
)
from services.diarynz import (
    cleanup_session_cache, get_diary_schedule, get_grade_events,
    get_diary_homework, get_homework_events,
)
from services.diaryhuman import get_diary_schedule_human, get_diary_homework_human
from services.digest import has_lessons, has_conf_link, build_digest_text
from utils import safe_send
import gc

logger = logging.getLogger(__name__)

LESSON_TIMES = ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]
LEAD_MIN = 5
LESSON_LINE_RE = re.compile(
    r"^\s*\d+\.\s*(?:<i>)?(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})(?:</i>)?\s*(.+?):\s*(.+)\s*$"
)

DIGEST_HOUR = 7
DIGEST_MINUTE = 30
DIGEST_ACTIVE_DAYS = 14  # кого вважаємо живим і варто скрапити щоранку

# Кеш часів уроків Human на день: {user_id: {"date", "fetched_at", "lessons"}}.
# Кешуємо тільки СТРУКТУРУ розкладу (коли уроки), а не посилання:
# у вікні нагадування скрапимо заново, щоб зловити посилання,
# яке вчитель додав в останній момент.
HUMAN_SCHED_CACHE = {}
HUMAN_REFRESH_SEC = 15 * 60  # періодичне оновлення — щоб побачити нові уроки

BACKUP_DIR = "backups"
BACKUP_KEEP = 7


async def sleep_to_next_minute():
    """Чекає до початку наступної хвилини (щоб запускатися рівно в :00 секунд)."""
    now = datetime.datetime.now(tz=KYIV_TZ)
    nxt = (now + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    await asyncio.sleep((nxt - now).total_seconds())


def parse_human_schedule_text(schedule_text: str) -> list[dict]:
    """Парсить текст розкладу Human у структуру."""
    lessons = []
    for line in schedule_text.splitlines():
        m = LESSON_LINE_RE.match(line.strip())
        if not m:
            continue
        start_s, end_s, subject, link = m.groups()
        link = link.strip()
        if link == "—":
            link = ""
        lessons.append({
            "start": start_s,
            "end": end_s,
            "subject": subject.strip(),
            "link": link,
        })
    return lessons


def build_dt_for_today(hhmm: str, now: datetime.datetime) -> datetime.datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


async def check_lessons():
    while True:
        users = db.get_users_with_notify()
        now_ts = int(time.time())

        tasks = []
        for user_id, login, enc_password, provider in users:
            try:
                vip_flag, expires = db.get_vip_status(user_id)
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if not login or not enc_password or not is_vip:
                    continue
                if provider == "nz":
                    tasks.append(_check_lessons_nz(user_id, login, enc_password))
                elif provider == "human":
                    tasks.append(_check_lessons_human(user_id, login, enc_password))
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
            schedule_text = await asyncio.to_thread(
                get_diary_schedule,
                login,
                password,
                days=["сьогодні"],
                user_id=user_id,
                db=db,
                fernet=fernet
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
        if await safe_send(user_id, text, disable_notify_on_block=True,
                           parse_mode="HTML", disable_web_page_preview=False):
            SENT_REMINDERS.add((user_id, target_key))
    except Exception:
        logger.exception("NZ lesson reminder failed for user_id=%s", user_id)


def _human_key(now: datetime.datetime, les: dict) -> str:
    return f"human|{now.date().isoformat()}|{les['start']}|{les['subject']}"


def _human_pending_window(lessons: list[dict], now: datetime.datetime, user_id: int) -> bool:
    """Чи є урок, для якого зараз вікно нагадування і ще не слали."""
    for les in lessons:
        start_dt = build_dt_for_today(les["start"], now)
        notify_dt = start_dt - datetime.timedelta(minutes=LEAD_MIN)
        if notify_dt <= now < start_dt and (user_id, _human_key(now, les)) not in SENT_REMINDERS:
            return True
    return False


async def _check_lessons_human(user_id: int, login: str, enc_password: str):
    """Human: часи уроків беремо з денного кешу, скрапимо тільки коли
    (1) кешу ще нема, (2) настав час планового оновлення, або
    (3) якийсь урок у вікні нагадування — тоді скрап свіжий, і посилання,
    додане в останній момент, не загубиться."""
    try:
        now = datetime.datetime.now(KYIV_TZ)
        today = now.date().isoformat()
        cache = HUMAN_SCHED_CACHE.get(user_id)

        need_scrape = (
            not cache
            or cache["date"] != today
            or (now.timestamp() - cache["fetched_at"]) >= HUMAN_REFRESH_SEC
            or _human_pending_window(cache["lessons"], now, user_id)
        )
        if not need_scrape:
            return

        password = fernet.decrypt(enc_password.encode()).decode()
        async with SEMAPHORE:
            schedule_text = await asyncio.to_thread(
                get_diary_schedule_human, login, password, ["сьогодні"]
            )

        lessons = parse_human_schedule_text(schedule_text)
        lessons.sort(key=lambda x: x["start"])

        HUMAN_SCHED_CACHE[user_id] = {
            "date": today,
            "fetched_at": now.timestamp(),
            "lessons": [{"start": l["start"], "subject": l["subject"]} for l in lessons],
        }

        for les in lessons:
            start_dt = build_dt_for_today(les["start"], now)
            notify_dt = start_dt - datetime.timedelta(minutes=LEAD_MIN)
            if not (notify_dt <= now < start_dt):
                continue

            link = les.get("link") or ""
            if not link or not has_conf_link(link):
                continue  # посилання ще нема — перевіримо наступної хвилини

            key = _human_key(now, les)
            if (user_id, key) in SENT_REMINDERS:
                continue

            text = (
                f"🔔 Нагадування: через {LEAD_MIN} хвилин починається "
                f"<b>{escape(les['subject'])}</b> о <b>{escape(les['start'])}</b>\n"
                f"{escape(link)}"
            )
            if await safe_send(user_id, text, disable_notify_on_block=True,
                               parse_mode="HTML", disable_web_page_preview=False):
                SENT_REMINDERS.add((user_id, key))
            await asyncio.sleep(0.25)
    except Exception:
        logger.exception("Human lesson reminder failed for user_id=%s", user_id)


async def check_grades():
    while True:
        users = db.get_users_with_grades_notify()  # (user_id, login, enc_password)
        for user_id, login, enc_password, provider in users:
            try:
                if provider != 'nz':
                    continue
                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if not is_vip or not login or not enc_password:
                    continue

                password = fernet.decrypt(enc_password.encode()).decode()

                async with SEMAPHORE:
                    events = await asyncio.to_thread(
                        get_grade_events,
                        login,
                        password,
                        20,
                        user_id=user_id,
                        db=db,
                        fernet=fernet
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
                    if await safe_send(user_id, "\n\n".join(lines),
                                       disable_notify_on_block=True,
                                       disable_web_page_preview=True):
                        db.set_last_grade_hashes(user_id, [e["hash"] for e in events[:3]])
                    await asyncio.sleep(0.25)

            except Exception:
                logger.exception("Grade notification check failed for user_id=%s provider=%s", user_id, provider)

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

        # 2. SENT_REMINDERS: ключі формату "provider|YYYY-MM-DD|..." —
        # прибираємо все, що не за сьогодні
        stale = {
            item for item in SENT_REMINDERS
            if len(item[1].split("|")) < 2 or item[1].split("|")[1] != today
        }
        SENT_REMINDERS.difference_update(stale)

        # 3. Кеш розкладу Human за минулі дні
        for uid in [uid for uid, c in HUMAN_SCHED_CACHE.items() if c["date"] != today]:
            HUMAN_SCHED_CACHE.pop(uid, None)

        # 4. Рейт-ліміти, старші за годину (інакше словник росте безмежно)
        cutoff = time.time() - 3600
        for key in [k for k, ts in USER_LAST_CALL.items() if ts < cutoff]:
            USER_LAST_CALL.pop(key, None)

        # 5. Завислі FSM-стани (юзер кинув авторизацію і пішов)
        try:
            db.fsm_purge_older_than(48 * 3600)
        except Exception:
            logger.exception("FSM purge failed")

        # 6. Примусовий збір сміття — прибирає «висячі» об'єкти
        # картинок і буферів, важливо на VPS з малим обсягом RAM
        gc.collect()


async def _fetch_digest_parts(user_id: int, login: str, enc_password: str, provider: str):
    """Тягне розклад і ДЗ на сьогодні. Повертає (schedule, homework)."""
    password = fernet.decrypt(enc_password.encode()).decode()

    async with SEMAPHORE:
        if provider == "human":
            schedule = await asyncio.to_thread(
                get_diary_schedule_human, login, password, ["сьогодні"]
            )
        else:
            schedule = await asyncio.to_thread(
                get_diary_schedule, login, password,
                days=["сьогодні"], user_id=user_id, db=db, fernet=fernet
            )

    if not has_lessons(schedule):
        return schedule, ""

    homework = ""
    try:
        async with SEMAPHORE:
            if provider == "human":
                homework = await asyncio.to_thread(
                    get_diary_homework_human, login, password, "today"
                )
            else:
                homework = await asyncio.to_thread(
                    get_diary_homework, login, password,
                    days=["сьогодні"], user_id=user_id, db=db, fernet=fernet
                )
    except Exception:
        # розклад важливіший за ДЗ — без нього дайджест все одно корисний
        logger.exception("Digest homework fetch failed for user_id=%s", user_id)

    return schedule, homework


async def send_digest_to(user_id: int, login: str, enc_password: str, provider: str, is_vip: bool) -> bool:
    """True якщо дайджест надіслано. Порожній день — не надсилаємо нічого."""
    try:
        schedule, homework = await _fetch_digest_parts(user_id, login, enc_password, provider)
        if not has_lessons(schedule):
            return False

        kb = None if is_vip else InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Хочу щоранку", callback_data="vip_menu")]
        ])

        return await safe_send(
            user_id,
            build_digest_text(schedule, homework, is_vip),
            disable_notify_on_block=True,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=kb
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
                if is_monday or row[4]  # row[4] = is_vip
            ]
            logger.info("Morning digest: %s recipients (monday=%s)", len(recipients), is_monday)

            sent = 0
            # чанками, щоб не тримати тисячу корутин на low-RAM VPS
            for i in range(0, len(recipients), 25):
                chunk = recipients[i:i + 25]
                results = await asyncio.gather(*[
                    send_digest_to(uid, login, pwd, provider, is_vip)
                    for uid, login, pwd, provider, is_vip in chunk
                ])
                sent += sum(1 for ok in results if ok)
                await asyncio.sleep(1)

            logger.info("Morning digest done: sent=%s", sent)
        except Exception:
            logger.exception("Morning digest task failed")


async def check_homework():
    """Пуш про НОВЕ ДЗ. Тільки NZ: у Human інша структура і 30 юзерів."""
    while True:
        for user_id, login, enc_password, provider in db.get_users_with_homework_notify():
            try:
                if provider != "nz" or not login or not enc_password:
                    continue

                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                if not (bool(vip_flag) and (expires == 0 or expires > now_ts)):
                    continue

                password = fernet.decrypt(enc_password.encode()).decode()
                async with SEMAPHORE:
                    events = await asyncio.to_thread(
                        get_homework_events, login, password,
                        days=["сьогодні", "завтра"],
                        user_id=user_id, db=db, fernet=fernet
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
                if await safe_send(user_id, "\n".join(lines),
                                   disable_notify_on_block=True,
                                   parse_mode="HTML",
                                   disable_web_page_preview=True):
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
                if await safe_send(
                    user_id,
                    "⏳ <b>Твій VIP закінчується завтра!</b>\n"
                    "Після цього вимкнуться ⏰ нагадування перед уроками "
                    "і 🔔 сповіщення про оцінки.\n\n"
                    "Продовжити: /vip",
                    disable_notify_on_block=True,
                    parse_mode="HTML"
                ):
                    db.set_expiry_stage(user_id, 1)
                await asyncio.sleep(0.25)

            winback_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Місяць за 50 ⭐️ (-33%)", callback_data="buy_winback")]
            ])
            for user_id, expires in db.get_vips_just_expired(WINBACK_GRACE_SEC):
                if await safe_send(
                    user_id,
                    "😔 <b>VIP закінчився</b> — нагадування і сповіщення вимкнено.\n\n"
                    "🎁 Тільки <b>48 годин</b>: місяць VIP за <b>50 ⭐️ замість 75</b>",
                    disable_notify_on_block=True,
                    parse_mode="HTML",
                    reply_markup=winback_kb
                ):
                    db.set_expiry_stage(user_id, 2)
                    db.record_command_metric("funnel:winback_sent", 0)
                await asyncio.sleep(0.25)
        except Exception:
            logger.exception("VIP expiry check failed")

        await asyncio.sleep(3600)


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
