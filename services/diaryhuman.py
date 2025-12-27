# diaryhuman.py
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from html import escape
from typing import Any
import xml.etree.ElementTree as ET

import pytz
import requests

KYIV_TZ = pytz.timezone("Europe/Kiev")
API = "https://api.human.ua/v1"

UA_WEEKDAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "Пʼятниця", "Субота", "Неділя"]


@dataclass
class Lesson:
    start: dt.datetime
    end: dt.datetime
    subject: str
    link: str | None


def _parse_date_any(s: str) -> dt.date | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _resolve_days(days: list[str] | None) -> list[tuple[str, dt.date]]:
    """Повертає список (label, date) у тому ж порядку, без дублікатів."""
    if days is None:
        days = ["сьогодні", "завтра"]

    today = dt.datetime.now(KYIV_TZ).date()

    # для дней недели (ua/ru)
    wd_map = {
        "понеділок": 0,
        "вівторок": 1,
        "середа": 2,
        "четвер": 3,
        "пʼятниця": 4,
        "п'ятниця": 4,
        "субота": 5,
        "неділя": 6,
    }

    out: list[tuple[str, dt.date]] = []
    seen: set[dt.date] = set()

    for raw in days:
        key = (raw or "").strip().lower()
        key2 = key.replace("’", "'").replace("ʼ", "'")

        if key in ("сьогодні", "сегодня"):
            d = today
            label = "Сьогодні"
        elif key == "завтра":
            d = today + dt.timedelta(days=1)
            label = "Завтра"
        elif key in wd_map or key2 in wd_map:
            wd = wd_map.get(key, wd_map.get(key2))
            week_start = today - dt.timedelta(days=today.weekday())
            d = week_start + dt.timedelta(days=wd)
            label = UA_WEEKDAYS[d.weekday()]
        else:
            parsed = _parse_date_any(raw)
            if not parsed:
                # неизвестный ввод — просто пропускаем
                continue
            d = parsed
            label = UA_WEEKDAYS[d.weekday()]

        if d not in seen:
            seen.add(d)
            out.append((label, d))

    return out


def _human_login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://id.human.ua",
        "referer": "https://id.human.ua/",
        "user-agent": "Mozilla/5.0",
    })

    r = s.post(f"{API}/auth", json={"email": email, "password": password}, timeout=25)
    if r.status_code != 200:
        # иногда API отдаёт текст/JSON с ошибкой — покажем аккуратно
        try:
            j = r.json()
            msg = j.get("message") or j.get("error") or str(j)
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"Помилка входу в Human ({r.status_code}): {msg}")

    return s


def _get_institution_user_id(s: requests.Session) -> int:
    r = s.get(
        f"{API}/user/institutions",
        params={"expand": "user,userTariff.tariffPlan", "page": 1, "_limit": 30},
        timeout=25,
    )
    r.raise_for_status()
    j = r.json()

    # пробуем самые частые формы
    insts = None
    if isinstance(j, dict):
        insts = j.get("institutions") or j.get("data") or j.get("items")
    if insts is None:
        insts = j

    if isinstance(insts, dict):
        insts = insts.get("items") or insts.get("data") or []

    if not isinstance(insts, list) or not insts:
        raise RuntimeError("Human: порожня відповідь, не знайдено institution_user_id")

    return int(insts[0]["id"])


def _fetch_calendar(s: requests.Session, institution_user_id: int, start_ts: int, finish_ts: int) -> dict[str, Any]:
    r = s.get(
        f"{API}/{institution_user_id}/calendar",
        params={
            "dateStart": start_ts,
            "dateFinish": finish_ts,
            "expand": "group.subject,webConference,classroom",
            "_limit": 987654321,
        },
        timeout=25,
    )
    r.raise_for_status()
    return r.json()


def _subject_name(lesson_event: dict[str, Any]) -> str:
    group = lesson_event.get("group") or {}
    subj = group.get("subject") or {}
    i18n = subj.get("i18n") or {}
    return (
            i18n.get("name")
            or i18n.get("short_name")
            or subj.get("name")
            or "——"
    )


def _webconf_url(lesson_event: dict[str, Any]) -> str | None:
    wc = lesson_event.get("webConference")
    if isinstance(wc, dict):
        return wc.get("url") or None
    if isinstance(wc, list):
        for item in wc:
            if isinstance(item, dict) and item.get("url"):
                return item["url"]
    return None


def _match_postevent_url(post_events: list[dict[str, Any]], lesson_dt: dt.datetime, subject: str) -> str | None:
    """Якщо webConference порожній — пробуємо взяти посилання з postEvents."""
    same_day = [p for p in post_events if p.get("url") and int(p.get("started_at", 0)) > 0]

    # фільтр за днем
    same_day = [
        p for p in same_day
        if dt.datetime.fromtimestamp(int(p["started_at"]), tz=KYIV_TZ).date() == lesson_dt.date()
    ]
    if not same_day:
        return None

    # 1) за близьким часом
    best = None
    best_abs = None
    for p in same_day:
        pdt = dt.datetime.fromtimestamp(int(p["started_at"]), tz=KYIV_TZ)
        diff = abs((pdt - lesson_dt).total_seconds())
        if best_abs is None or diff < best_abs:
            best_abs = diff
            best = p

    if best and best_abs is not None and best_abs <= 20 * 60:  # 20 минут
        return best.get("url") or None

    # 2) за назвою предмета в title
    subj_l = (subject or "").lower()
    if subj_l:
        for p in same_day:
            title = (p.get("title") or "").lower()
            if subj_l in title:
                return p.get("url") or None

    return None


def get_diary_schedule_human(email: str, password: str, days: list[str] | None = None) -> str:
    """
    Human schedule → Telegram HTML (как diarynz).
    Возвращает уже готовый текст для message.answer(..., parse_mode="HTML")
    """
    wanted = _resolve_days(days)
    if not wanted:
        return "Не зрозумів, за які дні показати розклад 🙃"

    wanted_dates = [d for _, d in wanted]
    min_d, max_d = min(wanted_dates), max(wanted_dates)

    # берём диапазон с небольшим запасом
    start_dt = KYIV_TZ.localize(dt.datetime.combine(min_d, dt.time(0, 0))) - dt.timedelta(days=1)
    finish_dt = KYIV_TZ.localize(dt.datetime.combine(max_d + dt.timedelta(days=1), dt.time(0, 0))) + dt.timedelta(
        days=1)

    s = _human_login(email, password)
    inst_user_id = _get_institution_user_id(s)
    cal = _fetch_calendar(s, inst_user_id, int(start_dt.timestamp()), int(finish_dt.timestamp()))

    lesson_events = cal.get("lessonEvents") or []
    post_events = cal.get("postEvents") or []

    # собираем уроки по датам
    lessons_by_date: dict[dt.date, list[Lesson]] = {d: [] for d in wanted_dates}

    for e in lesson_events:
        if not isinstance(e, dict):
            continue
        if "date" not in e or "date_end" not in e:
            continue

        # ВАЖНО: используем unix time (date/date_end) — он даёт правильное локальное время.
        start = dt.datetime.fromtimestamp(int(e["date"]), tz=KYIV_TZ)
        end = dt.datetime.fromtimestamp(int(e["date_end"]), tz=KYIV_TZ)
        d = start.date()
        if d not in lessons_by_date:
            continue

        subject = _subject_name(e)
        link = _webconf_url(e)
        if not link:
            link = _match_postevent_url(post_events, start, subject)

        lessons_by_date[d].append(Lesson(start=start, end=end, subject=subject, link=link))

    # формируем текст
    parts: list[str] = []
    for label, d in wanted:
        day_header = escape(label)
        items = sorted(lessons_by_date.get(d, []), key=lambda x: x.start)

        date_str = d.strftime("%d.%m.%Y")
        parts.append(f"📅 <b>{day_header} {date_str}</b>")
        if not items:
            parts.append("—\n")
            continue

        for idx, les in enumerate(items, start=1):
            time_str = f"{les.start:%H:%M} - {les.end:%H:%M}"
            subj = escape(les.subject)
            link = escape(les.link, quote=False) if les.link else "—"
            parts.append(f"{idx}. <i>{time_str}</i> {subj}: {link}")
        parts.append("")  # пустая строка между днями

    return "\n".join(parts).strip()


# ---------- homework ----------
@dataclass
class HWItem:
    due: dt.date
    subject: str
    theme: str
    type_short: str
    status: int  # 0/1/2


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _json_or_xml(resp: requests.Response) -> Any:
    # JSON если получилось
    try:
        return resp.json()
    except Exception:
        pass
    # иначе XML
    txt = resp.text.strip()
    if not txt.startswith("<"):
        raise RuntimeError("Не вдалося розібрати відповідь (не JSON і не XML)")
    return ET.fromstring(txt)


def _xml_text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def _parse_homework_payload(payload: Any) -> list[HWItem]:
    out: list[HWItem] = []

    # JSON может быть list или dict
    if isinstance(payload, list):
        tasks = payload
    elif isinstance(payload, dict):
        tasks = payload.get("userHomeTasks") or payload.get("items") or payload.get("data") or []
        if isinstance(tasks, dict):
            tasks = tasks.get("items") or tasks.get("data") or []
    else:
        tasks = []

    if not isinstance(tasks, list):
        tasks = []

    for t in tasks:
        if not isinstance(t, dict):
            continue

        due_ts = _safe_int(t.get("expire_date"))
        if not due_ts:
            continue
        due_date = dt.datetime.fromtimestamp(due_ts, tz=KYIV_TZ).date()

        # предмет
        subject = "—"
        group = t.get("group") or {}
        subj = group.get("subject") or {}
        i18n = subj.get("i18n") or {}
        subject = i18n.get("name") or i18n.get("short_name") or subj.get("name") or "—"

        # тема/текст
        theme_obj = t.get("theme") or {}
        theme = theme_obj.get("title") or t.get("title") or "—"

        # тип
        type_obj = t.get("type") or {}
        type_short = type_obj.get("short_name") or type_obj.get("name") or ""

        # статус
        status = 0
        htu = t.get("home_tasks_user")
        if isinstance(htu, dict):
            status = _safe_int(htu.get("status"), 0)
        else:
            users = t.get("homeTasksUsers") or []
            if isinstance(users, list) and users:
                status = _safe_int(users[0].get("status"), 0)

        out.append(HWItem(
            due=due_date,
            subject=str(subject),
            theme=str(theme),
            type_short=str(type_short),
            status=status
        ))

    return out


def _fetch_homework(s: requests.Session, institution_user_id: int, filter_name: str) -> Any:
    r = s.get(
        f"{API}/{institution_user_id}/home-task/home-task/students-tasks",
        params={
            "expand": "type.name,group.subject,home_tasks_user.assessment",
            "filter": filter_name,  # receivedWeek / receivedToday / receivedTomorrow
            "_limit": 987654321,
        },
        timeout=25,
    )
    r.raise_for_status()
    return _json_or_xml(r)


def _status_emoji(status: int) -> str:
    # 0 — не виконано; 2 часто означає «здано»
    if status == 2:
        return "✅"
    if status == 1:
        return "🟡"
    return "⏳"


def get_diary_homework_human(email: str, password: str, mode: str = "week") -> str:
    """
    mode: "week" | "today" | "tomorrow"
    Возвращает Telegram HTML.
    """
    filter_map = {
        "week": "receivedWeek",
        "today": "receivedToday",
        "tomorrow": "receivedTomorrow",
    }
    if mode not in filter_map:
        mode = "week"

    s = _human_login(email, password)
    inst_user_id = _get_institution_user_id(s)

    payload = _fetch_homework(s, inst_user_id, filter_map[mode])
    items = _parse_homework_payload(payload)

    if not items:
        title = {"week": "на тиждень", "today": "на сьогодні", "tomorrow": "на завтра"}[mode]
        return f"📚 <b>ДЗ {escape(title)}</b>\n—"

    # группируем по дате дедлайна
    by_day: dict[dt.date, list[HWItem]] = {}
    for it in items:
        by_day.setdefault(it.due, []).append(it)

    # сортировки
    days_sorted = sorted(by_day.keys())
    for d in days_sorted:
        by_day[d].sort(key=lambda x: (x.subject, x.type_short, x.theme))

    header_map = {"week": "📚 <b>ДЗ на тиждень</b>", "today": "📚 <b>ДЗ на сьогодні</b>",
                  "tomorrow": "📚 <b>ДЗ на завтра</b>"}
    parts: list[str] = [header_map[mode], ""]

    for d in days_sorted:
        day_name = UA_WEEKDAYS[d.weekday()]
        date_str = d.strftime("%d.%m.%Y")
        parts.append(f"📅 <b>{escape(day_name)} {escape(date_str)}</b>")

        for i, it in enumerate(by_day[d], start=1):
            st = _status_emoji(it.status)
            subj = escape(it.subject)
            typ = escape(it.type_short) if it.type_short else ""
            theme = escape(it.theme)

            # формат: "1) ✅ Геометрія (д.з.): Тема..."
            mid = f" ({typ})" if typ else ""
            parts.append(f"{i}. {st} <b>{subj}</b>{mid}: {theme}")

        parts.append("")

    return "\n".join(parts).strip()


# ---------- news / feed ----------

def _fmt_dt(ts: int) -> str:
    """Unix ts -> '20.12.2025 о 12:29' (Kyiv)."""
    try:
        d = dt.datetime.fromtimestamp(int(ts), tz=KYIV_TZ)
        return d.strftime("%d.%m.%Y о %H:%M")
    except Exception:
        return ""


def _parse_feed_payload(payload: Any) -> list[dict[str, Any]]:
    """
    Human feed может прийти:
    - JSON list: [{...}, {...}]
    - JSON dict: {"items":[...]} или {"data":[...]}
    - XML: <response><item>...</item></response>
    Возвращаем список items как list[dict] или list[xml-element->dict-like] не надо,
    мы сразу сведём к list[dict] (для XML руками достанем поля).
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("data") or payload.get("posts") or payload.get("feed") or []
        if isinstance(items, dict):
            items = items.get("items") or items.get("data") or []
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
        return []

    # XML — вернём спец-маркер: список ET.Element
    if isinstance(payload, ET.Element):
        # обычно <response><item>...</item></response>
        # но на всякий — .//item
        return payload.findall(".//item")  # type: ignore[return-value]

    return []


def _fetch_news(s: requests.Session, institution_user_id: int, limit: int = 10, page: int = 1) -> Any:
    r = s.get(
        f"{API}/{institution_user_id}/feed/post/global",
        params={
            "_limit": limit,
            "page": page,
            "expand": "homeTask.type,homeTask.theme,lessonTask.theme,group.subject,comments,lessonTask.contentWithoutMongo",
        },
        timeout=25,
    )
    r.raise_for_status()
    return _json_or_xml(r)


def get_diary_news_human(email: str, password: str, limit: int = 10, page: int = 1) -> str:
    """
    Human news/feed → Telegram HTML (как news в diarynz по смыслу).
    """
    s = _human_login(email, password)
    inst_user_id = _get_institution_user_id(s)

    payload = _fetch_news(s, inst_user_id, limit=limit, page=page)
    items = _parse_feed_payload(payload)

    if not items:
        return "📰 <b>Новини</b>\n—"

    out: list[str] = ["📰 <b>Новини</b>", ""]

    # ---- JSON items (list[dict]) ----
    if items and isinstance(items[0], dict):
        for it in items:  # type: ignore[assignment]
            # teacher/owner
            owner = it.get("owner") or {}
            teacher = " ".join([
                str(owner.get("last_name") or "").strip(),
                str(owner.get("first_name") or "").strip(),
                str(owner.get("patronymic") or "").strip(),
            ]).strip() or "Вчитель"

            published_at = it.get("published_at") or it.get("created_at") or it.get("updated_at") or 0
            when = _fmt_dt(_safe_int(published_at, 0))

            # subject
            subject = "—"
            group = it.get("group") or {}
            subj = group.get("subject") or {}
            i18n = subj.get("i18n") or {}
            subject = i18n.get("name") or i18n.get("short_name") or subj.get("name") or "—"

            # 1) обычный пост
            post_text = ""
            pc = it.get("postsContent") or it.get("postContent") or {}
            if isinstance(pc, dict):
                post_text = (pc.get("text") or "").strip()

            # 2) homeTask (если новость про ДЗ/задачу)
            home_task = it.get("homeTask") or {}
            ht_type = ""
            ht_theme = ""
            ht_deadline = ""
            if isinstance(home_task, dict) and home_task:
                typ = home_task.get("type") or {}
                if isinstance(typ, dict):
                    ht_type = (typ.get("short_name") or typ.get("name") or "").strip()
                theme = home_task.get("theme") or {}
                if isinstance(theme, dict):
                    ht_theme = (theme.get("title") or "").strip()
                expire_ts = _safe_int(home_task.get("expire_date"), 0)
                ht_deadline = _fmt_dt(expire_ts) if expire_ts >= 946684800 else ""  # якщо немає дедлайну — не показуємо

            header = f"• <b>{escape(teacher)}</b>"
            if when:
                header += f" — {escape(when)}"

            # форматирование как “news”
            if post_text:
                # учитель + текст (и если есть предмет — добавим строкой)
                if subject and subject != "—":
                    out.append(f"{header}\n{escape(subject)}\n{escape(post_text)}")
                else:
                    out.append(f"{header}\n{escape(post_text)}")
                out.append("")
                continue

            if ht_type or ht_theme:
                line1 = f"{escape(subject)}: {escape(ht_type) if ht_type else 'Завдання'}"
                line2 = escape(ht_theme) if ht_theme else "—"
                if ht_deadline:
                    line2 += f" <i>(до {escape(ht_deadline)})</i>"
                out.append(f"{header}\n{line1}\n{line2}")
                out.append("")
                continue

            # fallback
            out.append(f"{header}\n(Без тексту)")
            out.append("")

        return "\n".join(out).strip()

    # ---- XML items (list[ET.Element]) ----
    for it in items:  # type: ignore[assignment]
        if not isinstance(it, ET.Element):
            continue

        teacher = " ".join([
            _xml_text(it, "owner/last_name"),
            _xml_text(it, "owner/first_name"),
            _xml_text(it, "owner/patronymic"),
        ]).strip() or "Вчитель"

        when = _fmt_dt(_safe_int(_xml_text(it, "published_at"), 0))

        subject = _xml_text(it, "group/subject/i18n/name") or "—"
        post_text = _xml_text(it, "postsContent/text")

        ht_type = _xml_text(it, "homeTask/type/short_name") or _xml_text(it, "homeTask/type/name")
        ht_theme = _xml_text(it, "homeTask/theme/title")
        expire_ts = _safe_int(_xml_text(it, "homeTask/expire_date"), 0)
        ht_deadline = _fmt_dt(expire_ts) if expire_ts >= 946684800 else ""  # < 01.01.2000 -> нема дедлайну

        header = f"• <b>{escape(teacher)}</b>"
        if when:
            header += f" — {escape(when)}"

        if post_text:
            if subject and subject != "—":
                out.append(f"{header}\n{escape(subject)}\n{escape(post_text)}")
            else:
                out.append(f"{header}\n{escape(post_text)}")
            out.append("")
            continue

        if ht_type or ht_theme:
            line1 = f"{escape(subject)}: {escape(ht_type) if ht_type else 'Завдання'}"
            line2 = escape(ht_theme) if ht_theme else "—"
            if ht_deadline:
                line2 += f" <i>(до {escape(ht_deadline)})</i>"
            out.append(f"{header}\n{line1}\n{line2}")
            out.append("")
            continue

        out.append(f"{header}\n(Без тексту)")
        out.append("")

    return "\n".join(out).strip()


# ---------- grades / average (human analytics) ----------

def _get_institution_meta(s: requests.Session) -> tuple[int, int]:
    """
    Возвращает:
      (institution_user_id, institution_id)
    institution_user_id — тот самый 350823
    institution_id — тот самый 1751
    """
    r = s.get(
        f"{API}/user/institutions",
        params={"expand": "user,userTariff.tariffPlan,institution", "page": 1, "_limit": 30},
        timeout=25,
    )
    r.raise_for_status()
    j = r.json()

    insts = None
    if isinstance(j, dict):
        insts = j.get("institutions") or j.get("data") or j.get("items")
    if insts is None:
        insts = j
    if isinstance(insts, dict):
        insts = insts.get("items") or insts.get("data") or []

    if not isinstance(insts, list) or not insts:
        raise RuntimeError("Human: порожня відповідь, не знайдено institution_user_id / institution_id")

    first = insts[0]
    institution_user_id = int(first["id"])

    inst_obj = first.get("institution") or {}
    institution_id = _safe_int(inst_obj.get("id"), 0)
    if not institution_id:
        # иногда может быть плоское поле
        institution_id = _safe_int(first.get("institution_id"), 0)

    if not institution_id:
        raise RuntimeError("Human: не знайдено institution_id")

    return institution_user_id, int(institution_id)


def _date_range_ts(days_back: int = 30) -> tuple[int, int]:
    """
    date_from/date_to в unix, как в твоих примерах.
    Берём последние N дней (включая сегодня) по киевскому времени.
    """
    now = dt.datetime.now(KYIV_TZ)
    start_day = (now.date() - dt.timedelta(days=days_back))
    date_from = KYIV_TZ.localize(dt.datetime.combine(start_day, dt.time(0, 0, 0)))
    date_to = KYIV_TZ.localize(dt.datetime.combine(now.date(), dt.time(23, 59, 59)))
    return int(date_from.timestamp()), int(date_to.timestamp())


def _fmt_float(x: Any, digits: int = 2) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return "—"


def _parse_ddmmyyyy(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime((s or "").strip(), "%d.%m.%Y").date()
    except Exception:
        return None


def _fetch_assessments_average(
        s: requests.Session,
        institution_user_id: int,
        institution_id: int,
        date_from: int,
        date_to: int
) -> Any:
    r = s.get(
        f"{API}/{institution_user_id}/analytics/assessments/average",
        params={
            "entity_type[]": [1, 2],
            "sort": "-id",
            "date_to": date_to,
            "date_from": date_from,
            "student_id": institution_user_id,
            "learner_id": institution_user_id,
            "_limit": 50,
            "institution_id": institution_id,
        },
        timeout=25,
    )
    r.raise_for_status()
    return _json_or_xml(r)


def _fetch_assessments_performance(
        s: requests.Session,
        institution_user_id: int,
        institution_id: int,
        date_from: int,
        date_to: int
) -> Any:
    r = s.get(
        f"{API}/{institution_user_id}/analytics/assessments/performance",
        params={
            "entity_type[]": [1, 2],
            "sort": "-id",
            "date_to": date_to,
            "date_from": date_from,
            "student_id": institution_user_id,
            "learner_id": institution_user_id,
            "_limit": 50,
            "institution_id": institution_id,
        },
        timeout=25,
    )
    r.raise_for_status()
    return _json_or_xml(r)


def _fetch_assessments_average_dynamic(
        s: requests.Session,
        institution_user_id: int,
        institution_id: int,
        date_from: int,
        date_to: int
) -> Any:
    r = s.get(
        f"{API}/{institution_user_id}/analytics/assessments/average-dynamic",
        params={
            "entity_type[]": [1, 2],
            "sort": "-id",
            "date_to": date_to,
            "date_from": date_from,
            "student_id": institution_user_id,
            "learner_id": institution_user_id,
            "_limit": 50,
            "institution_id": institution_id,
        },
        timeout=25,
    )
    r.raise_for_status()
    return _json_or_xml(r)


def _fetch_assessments_detailed(s: requests.Session, inst_uid: int, inst_id: int, d_from: int, d_to: int) -> Any:
    """Тягне детальні оцінки для розрахунку 'Найкращий предмет'."""
    r = s.get(
        f"{API}/{inst_uid}/analytics/assessments",
        params={
            "entity_type[]": [1, 2],
            "sort": "-id",
            "date_to": d_to,
            "date_from": d_from,
            "student_id": inst_uid,
            "learner_id": inst_uid,
            "_limit": 100,  # беремо більше, щоб точно влізли за тиждень
            "institution_id": inst_id,
            "expand": "theme,group.subject",
        },
        timeout=25,
    )
    r.raise_for_status()
    return _json_or_xml(r)


def get_diary_grades_human(email: str, password: str, days_back: int = 30, last_days: int = 7) -> str:
    """
    Human средний бал / динамика / распределение.
    Возвращает Telegram HTML.
    """
    s = _human_login(email, password)
    institution_user_id, institution_id = _get_institution_meta(s)

    date_from, date_to = _date_range_ts(days_back=days_back)

    avg_payload = _fetch_assessments_average(s, institution_user_id, institution_id, date_from, date_to)
    perf_payload = _fetch_assessments_performance(s, institution_user_id, institution_id, date_from, date_to)
    dyn_payload = _fetch_assessments_average_dynamic(s, institution_user_id, institution_id, date_from, date_to)
    detailed_payload = _fetch_assessments_detailed(s, institution_user_id, institution_id, date_from, date_to)
    # --- Calculation: Best Subject (Last 7 days) ---
    best_subject_str = "—"
    if isinstance(detailed_payload, list):
        now_ts = dt.datetime.now(KYIV_TZ).timestamp()
        week_ago_ts = now_ts - (7 * 24 * 60 * 60)

        subject_grades: dict[str, list[int]] = {}

        for item in detailed_payload:
            if not isinstance(item, dict): continue

            # Перевірка дати (останні 7 днів)
            item_date = _safe_int(item.get("date_from")) or _safe_int(item.get("date")) or 0
            if item_date < week_ago_ts:
                continue

            grade = _safe_int(item.get("int_value"))
            if not grade: continue

            # Дістаємо назву
            subj_name = "—"
            try:
                subj_name = item['group']['subject']['i18n']['short_name']
            except (KeyError, TypeError):
                continue

            if subj_name not in subject_grades:
                subject_grades[subj_name] = []
            subject_grades[subj_name].append(grade)

        # Рахуємо середній для кожного
        if subject_grades:
            averages = {k: sum(v) / len(v) for k, v in subject_grades.items()}
            best_sub = max(averages, key=averages.get)
            best_val = averages[best_sub]
            best_subject_str = f"{escape(best_sub)} ({best_val:.1f})"
        else:
            best_subject_str = "Немає оцінок за тиждень"
    # --- average ---
    avg_value = None
    if isinstance(avg_payload, list) and avg_payload:
        if isinstance(avg_payload[0], dict):
            avg_value = avg_payload[0].get("average")
    elif isinstance(avg_payload, dict):
        # если вдруг вернёт {"average": "..."}
        avg_value = avg_payload.get("average")

    avg_str = _fmt_float(avg_value, 2)

    # --- performance levels ---
    # ожидаем list[{"level":"mid","count":"42"}, ...]
    lvl_counts: dict[str, int] = {"low": 0, "pre-mid": 0, "mid": 0, "hight": 0}
    if isinstance(perf_payload, list):
        for row in perf_payload:
            if not isinstance(row, dict):
                continue
            lvl = (row.get("level") or "").strip()
            cnt = _safe_int(row.get("count"), 0)
            if lvl:
                lvl_counts[lvl] = cnt

    total = sum(lvl_counts.values()) or 0

    def pct(n: int) -> str:
        if not total:
            return "0%"
        return f"{round(n * 100 / total)}%"

    lvl_lines = [
        f"🔴 <b>Низький</b>: {lvl_counts.get('low', 0)} <i>({pct(lvl_counts.get('low', 0))})</i>",
        f"🟡 <b>Нижче середнього</b>: {lvl_counts.get('pre-mid', 0)} <i>({pct(lvl_counts.get('pre-mid', 0))})</i>",
        f"🔵 <b>Середній</b>: {lvl_counts.get('mid', 0)} <i>({pct(lvl_counts.get('mid', 0))})</i>",
        f"🟢 <b>Високий</b>: {lvl_counts.get('hight', 0)} <i>({pct(lvl_counts.get('hight', 0))})</i>",
    ]

    # --- dynamic ---
    # ожидаем list[{"lesson_date":"21.11.2025","average":"9.3333",...}, ...]
    dyn_rows: list[tuple[dt.date, float]] = []
    if isinstance(dyn_payload, list):
        for row in dyn_payload:
            if not isinstance(row, dict):
                continue
            d = _parse_ddmmyyyy(str(row.get("lesson_date") or ""))
            if not d:
                continue
            try:
                a = float(row.get("average"))
            except Exception:
                continue
            dyn_rows.append((d, a))

    dyn_rows.sort(key=lambda x: x[0])  # по дате вверх
    last = dyn_rows[-last_days:] if last_days > 0 else dyn_rows

    dyn_lines: list[str] = []
    for d, a in reversed(last):  # показываем свежие сверху
        dyn_lines.append(f"• {d.strftime('%d.%m')}: <b>{a:.2f}</b>")

    # период строкой
    from_dt = dt.datetime.fromtimestamp(date_from, tz=KYIV_TZ).strftime("%d.%m.%Y")
    to_dt = dt.datetime.fromtimestamp(date_to, tz=KYIV_TZ).strftime("%d.%m.%Y")

    parts: list[str] = []
    parts.append("🈴 <b>Середній бал</b>")
    parts.append(f"📆 <i>Період:</i> {escape(from_dt)} — {escape(to_dt)}")
    parts.append(f"⭐️ <b>Середній:</b> {escape(avg_str)}")
    parts.append(f"🏆 <b>Предмет тижня:</b> {best_subject_str}")
    parts.append("")

    if total:
        parts.append("📊 <b>Розподіл оцінок</b>")
        parts.extend(lvl_lines)
        parts.append(f"Всього: <b>{total}</b>")
        parts.append("")

    if dyn_lines:
        parts.append(f"📈 <b>Динаміка (останні {last_days} дн.)</b>")
        parts.extend(dyn_lines)
    else:
        parts.append("📈 <b>Динаміка</b>\n—")

    return "\n".join(parts).strip()
