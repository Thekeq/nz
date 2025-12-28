import asyncio
import datetime
import time
import re
from html import escape
from loader import db, bot, SEMAPHORE, SENT_REMINDERS, WRAPPED_CACHE, HW_AI_CACHE, KYIV_TZ
from services.diarynz import get_diary_schedule, get_grade_events
from services.diaryhuman import get_diary_schedule_human
import gc

LESSON_TIMES = ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]
LEAD_MIN = 5


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


def _has_conf_link(s: str) -> bool:
    return ("meet.google.com" in s) or ("zoom.us" in s)


async def check_lessons():
    while True:
        now = datetime.datetime.now(KYIV_TZ)

        # берем всех сразу один раз в минуту
        users = db.get_users_with_notify()  # [(user_id, login, enc_password, provider), ...]

        for user_id, login, enc_password, provider in users:
            try:
                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if not login or not enc_password or not is_vip:
                    continue

                password = fernet.decrypt(enc_password.encode()).decode()

                # ---------------- NZ ----------------
                if provider == "nz":
                    # тут твоя стара логика, но она должна жить отдельно от human
                    for idx, lesson_time in enumerate(LESSON_TIMES, start=1):
                        h, m = map(int, lesson_time.split(":"))
                        lesson_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                        notify_dt = lesson_dt - datetime.timedelta(minutes=LEAD_MIN)

                        # 60 секундное окно
                        if 0 <= (now - notify_dt).total_seconds() < 60:
                            async with SEMAPHORE:
                                schedule_text = await asyncio.to_thread(
                                    get_diary_schedule, login, password, days=["сьогодні"]
                                )
                            # парсим строки "1. ...."
                            lessons_list = []
                            for line in schedule_text.splitlines():
                                m2 = re.match(r"\d+\.\s*(.*)", line)
                                if m2:
                                    lessons_list.append(m2.group(1).strip())

                            if len(lessons_list) >= idx:
                                lesson_name = lessons_list[idx - 1]
                                if ("https://meet.google.com" in lesson_name) or ("zoom.us" in lesson_name):
                                    text = (
                                        f"🔔 Нагадування: через {LEAD_MIN} хвилин починається "
                                        f"<b>{lesson_name}</b> о <b>{lesson_time}</b>."
                                    )
                                    await bot.send_message(
                                        user_id,
                                        text,
                                        parse_mode="HTML",
                                        disable_web_page_preview=False
                                    )
                                    await asyncio.sleep(0.25)
                            break  # NZ: за хвилину максимум 1 нагадування

                # ---------------- HUMAN ----------------
                elif provider == "human":
                    async with SEMAPHORE:
                        schedule_text = await asyncio.to_thread(
                            get_diary_schedule_human, login, password, ["сьогодні"]
                        )

                    lessons = parse_human_schedule_text(schedule_text)
                    if not lessons:
                        continue

                    # уроки отсортированы? на всякий
                    lessons.sort(key=lambda x: x["start"])

                    for les in lessons:
                        # les["start"] = "09:55"
                        start_dt = build_dt_for_today(les["start"], now)
                        notify_dt = start_dt - datetime.timedelta(minutes=LEAD_MIN)

                        if 0 <= (now - notify_dt).total_seconds() < 60:
                            link = les.get("link") or ""
                            if not link or not _has_conf_link(link):
                                break  # нет ссылки — нет смысла слать

                            key = f"{now.date().isoformat()}|{les['start']}|{les['subject']}"
                            if (user_id, key) in SENT_REMINDERS:
                                break
                            SENT_REMINDERS.add((user_id, key))

                            text = (
                                f"🔔 Нагадування: через {LEAD_MIN} хвилин починається "
                                f"<b>{escape(les['subject'])}</b> о <b>{escape(les['start'])}</b>\n"
                                f"{escape(link)}"
                            )
                            await bot.send_message(
                                user_id,
                                text,
                                parse_mode="HTML",
                                disable_web_page_preview=False
                            )
                            await asyncio.sleep(0.25)
                            break  # за хвилину максимум 1 нагадування

                else:
                    continue

            except Exception:
                pass

        await sleep_to_next_minute()


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
                    events = await asyncio.to_thread(get_grade_events, login, password, 20)

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
                    await bot.send_message(user_id, "\n\n".join(lines), disable_web_page_preview=True)

                    db.set_last_grade_hashes(user_id, [e["hash"] for e in events[:3]])
                    await asyncio.sleep(0.25)

            except Exception:
                pass

        await asyncio.sleep(60 * 10)


async def memory_cleaner_task():
    """Фоновая задача для очистки оперативной памяти"""
    while True:
        await asyncio.sleep(3600)  # Запускаем раз в 1 час (3600 сек)

        # 1. Очистка кеша Wrapped (он нужен только "здесь і зараз")
        # Если юзер захочет поделиться через час - сгенерирует заново.
        if WRAPPED_CACHE:
            WRAPPED_CACHE.clear()

        if HW_AI_CACHE:
            HW_AI_CACHE.clear()
        # 2. Очистка SENT_REMINDERS (только ночью, например, в 04:00 утра)
        now = datetime.datetime.now(KYIV_TZ)
        if now.hour == 4:
            if SENT_REMINDERS:
                SENT_REMINDERS.clear()

        # 3. Принудительный сбор мусора (самое важное для PythonAnywhere)
        # Это удаляет "висячие" объекты картинок и буферов
        gc.collect()
