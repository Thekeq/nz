import re
import logging
import json
import threading
import functools
import inspect
import time
from contextlib import contextmanager
from concurrent.futures import Future
from collections import defaultdict
from urllib.parse import urljoin
import hashlib
import cloudscraper
import requests
from bs4 import BeautifulSoup
import datetime

from services.digest import homework_hash

logger = logging.getLogger(__name__)
_SESSION_LOCKS: dict[int, threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()
_SCRAPER_CACHE: dict[int, dict] = {}
_SCRAPER_CACHE_TTL = 20 * 60
_INFLIGHT_CALLS: dict[tuple, Future] = {}
_INFLIGHT_GUARD = threading.Lock()


class InvalidCredentials(Exception):
    pass


LINK_PRIORITIES = [
    "meet.google.com",
    "zoom.us",
]

BASE = "https://nz.ua"

DEFAULT_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://nz.ua/",
    "Origin": "https://nz.ua",
}


def pick_best_link(box):
    links = [
        a.get("href")
        for a in box.select(".diary-lesson-text a")
        if a.get("href") and a.get("href").startswith("https://")
    ]

    if not links:
        return None

    for domain in LINK_PRIORITIES:
        for href in links:
            if domain in href:
                return href

    return links[0]


def _dump_cookies(scraper) -> str:
    cookies = []
    for cookie in scraper.cookies:
        rest = {}
        for key, value in (getattr(cookie, "_rest", {}) or {}).items():
            if value is None or isinstance(value, (str, int, float, bool)):
                rest[str(key)] = value
            else:
                rest[str(key)] = str(value)

        cookies.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or "",
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "expires": cookie.expires,
            "rest": rest,
        })

    payload = {
        "version": 2,
        "user_agent": scraper.headers.get("User-Agent"),
        "cookies": sorted(cookies, key=lambda c: (c["domain"], c["path"], c["name"])),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_cookies(scraper, raw_cookies: str):
    payload = json.loads(raw_cookies)

    if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        user_agent = payload.get("user_agent")
        if user_agent:
            scraper.headers.update({"User-Agent": user_agent})

        scraper.cookies.clear()
        for item in payload["cookies"]:
            name = item.get("name")
            value = item.get("value")
            if not name or value is None:
                continue

            cookie = requests.cookies.create_cookie(
                name=name,
                value=value,
                domain=item.get("domain") or "",
                path=item.get("path") or "/",
                secure=bool(item.get("secure")),
                expires=item.get("expires"),
                rest=item.get("rest") or {},
            )
            scraper.cookies.set_cookie(cookie)
        return

    # Legacy format: {"PHPSESSID": "...", ...}
    if isinstance(payload, dict):
        scraper.cookies.update(payload)


def _session_signature(raw_cookies: str):
    try:
        payload = json.loads(raw_cookies)
    except (TypeError, ValueError):
        return None

    if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        cookies = [
            (
                item.get("domain") or "",
                item.get("path") or "/",
                item.get("name"),
                item.get("value"),
                bool(item.get("secure")),
                item.get("expires"),
                tuple(sorted((item.get("rest") or {}).items())),
            )
            for item in payload["cookies"]
            if item.get("name")
        ]
        return payload.get("user_agent"), tuple(sorted(cookies))

    if isinstance(payload, dict):
        return None, tuple(sorted(payload.items()))

    return None


def _record_nz_event(db, event: str):
    if not db:
        return
    try:
        db.record_nz_session_event(event)
    except Exception:
        logger.exception("Failed to record NZ session metric event=%s", event)


def _get_session_lock(user_id: int) -> threading.RLock:
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(user_id)
        if lock is None:
            lock = threading.RLock()
            _SESSION_LOCKS[user_id] = lock
        return lock


@contextmanager
def _user_session_scope(user_id: int | None):
    if user_id is None:
        yield
        return

    lock = _get_session_lock(user_id)
    with lock:
        yield


def _with_user_session_lock(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        user_id = kwargs.get("user_id")
        if user_id is None:
            try:
                user_id = inspect.signature(func).bind_partial(*args, **kwargs).arguments.get("user_id")
            except TypeError:
                user_id = None

        with _user_session_scope(user_id):
            return func(*args, **kwargs)

    return wrapper


def _get_scraper(user_id: int | None):
    if user_id is None:
        return cloudscraper.create_scraper(), False

    now = time.time()
    with _SESSION_LOCKS_GUARD:
        item = _SCRAPER_CACHE.get(user_id)
        if item and now - item["last_used"] <= _SCRAPER_CACHE_TTL:
            item["last_used"] = now
            return item["scraper"], True

        if item:
            try:
                item["scraper"].close()
            except Exception:
                pass

        scraper = cloudscraper.create_scraper()
        _SCRAPER_CACHE[user_id] = {"scraper": scraper, "last_used": now}
        return scraper, False


def _release_scraper(user_id: int | None, scraper, keep: bool):
    if keep:
        if user_id is not None:
            with _SESSION_LOCKS_GUARD:
                item = _SCRAPER_CACHE.get(user_id)
                if item and item.get("scraper") is scraper:
                    item["last_used"] = time.time()
        return
    scraper.close()


def clear_user_session_cache(user_id: int):
    with _SESSION_LOCKS_GUARD:
        item = _SCRAPER_CACHE.pop(user_id, None)
    if item:
        try:
            item["scraper"].close()
        except Exception:
            pass


def cleanup_session_cache(max_idle: int = _SCRAPER_CACHE_TTL):
    now = time.time()
    expired = []
    with _SESSION_LOCKS_GUARD:
        for user_id, item in list(_SCRAPER_CACHE.items()):
            if now - item["last_used"] > max_idle:
                expired.append((user_id, item))
                _SCRAPER_CACHE.pop(user_id, None)

    for _, item in expired:
        try:
            item["scraper"].close()
        except Exception:
            pass


def _dedupe_key(func, args: tuple, kwargs: dict):
    user_id = kwargs.get("user_id")
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        arguments = dict(bound.arguments)
        user_id = user_id if user_id is not None else arguments.get("user_id")
        arguments.pop("password", None)
        arguments.pop("db", None)
        arguments.pop("fernet", None)
        stable_args = tuple(sorted((key, repr(value)) for key, value in arguments.items()))
        return func.__name__, user_id, stable_args
    except Exception:
        pass

    stable_kwargs = tuple(sorted(
        (key, repr(value))
        for key, value in kwargs.items()
        if key not in {"password", "db", "fernet"}
    ))
    safe_args = args[:1] + args[2:]
    return func.__name__, user_id, repr(safe_args), stable_kwargs


def _dedupe_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        key = _dedupe_key(func, args, kwargs)
        with _INFLIGHT_GUARD:
            future = _INFLIGHT_CALLS.get(key)
            if future is None:
                future = Future()
                _INFLIGHT_CALLS[key] = future
                owner = True
            else:
                owner = False

        if not owner:
            return future.result()

        try:
            value = func(*args, **kwargs)
            future.set_result(value)
            return value
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with _INFLIGHT_GUARD:
                _INFLIGHT_CALLS.pop(key, None)

    return wrapper


def _is_login_page(soup: BeautifulSoup) -> bool:
    return bool(
        soup.find("input", {"name": "_csrf"})
        and (
            soup.find("input", {"name": "LoginForm[login]"})
            or soup.find("input", {"name": "LoginForm[password]"})
            or soup.find("form", id="login-form")
        )
    )


def _session_looks_authorized(soup: BeautifulSoup) -> bool:
    if _is_login_page(soup):
        return False
    return bool(
        soup.select_one(".diary-item")
        or soup.select_one("table.marks-report")
        or soup.find("div", id="school-news-list")
        or soup.find("select", {"id": "personalselectform-semester_id"})
    )


def _login_nz(scraper, login: str, password: str):
    r = scraper.get("https://nz.ua/", timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    csrf_tag = soup.find("input", {"name": "_csrf"})
    if not csrf_tag:
        raise RuntimeError("Не удалось получить CSRF токен.")
    csrf = csrf_tag["value"]

    data = {
        "_csrf": csrf,
        "LoginForm[login]": login,
        "LoginForm[password]": password,
        "LoginForm[rememberMe]": ["0", "1"],
        "ajax": "login-form",
        "login-button": "undefined",
    }

    resp_val = scraper.post("https://nz.ua/login", data=data, headers=DEFAULT_HEADERS, timeout=10)
    try:
        j = resp_val.json()
    except ValueError:
        j = {}
    if j:
        msg = (j.get("loginform-password") or j.get("loginform-login") or ["Сталася невідома помилка"])[0]
        raise InvalidCredentials(msg)

    scraper.post("https://nz.ua/login", data=data, timeout=10)


def _extract_csrf(soup: BeautifulSoup, scraper=None) -> str | None:
    csrf_tag = soup.find("input", {"name": "_csrf"})
    if csrf_tag and csrf_tag.get("value"):
        return csrf_tag["value"]

    meta_tag = soup.find("meta", {"name": "csrf-token"})
    if meta_tag and meta_tag.get("content"):
        return meta_tag["content"]

    if scraper:
        resp = scraper.get("https://nz.ua/", timeout=10)
        root_soup = BeautifulSoup(resp.text, "html.parser")
        return _extract_csrf(root_soup)

    return None


def _selected_semester_id(select_box) -> str | None:
    selected_option = select_box.find("option", selected=True)
    return selected_option.get("value") if selected_option else None


def _current_semester_id(select_box) -> str | None:
    current_option = select_box.find("option", string=lambda text: text and "(поточний)" in text)
    return current_option.get("value") if current_option else None


def _change_semester(scraper, csrf: str, semester_id: str):
    scraper.post(
        f"{BASE}/site/semester-change",
        data={"_csrf": csrf, "semester_id": semester_id},
        headers=DEFAULT_HEADERS,
        timeout=10,
    )


def _ensure_current_semester(scraper, soup: BeautifulSoup, url: str, params: dict | None = None):
    semester_select = soup.find("select", {"id": "personalselectform-semester_id"})
    if not semester_select:
        return soup

    current_sem_id = _current_semester_id(semester_select)
    if not current_sem_id or _selected_semester_id(semester_select) == current_sem_id:
        return soup

    csrf = _extract_csrf(soup, scraper)
    if not csrf:
        return None

    _change_semester(scraper, csrf, current_sem_id)
    resp = scraper.get(url, params=params, timeout=10)
    return BeautifulSoup(resp.text, "html.parser")


def _open_authorized_page(
        scraper,
        user_id: int | None,
        login: str,
        password: str,
        db=None,
        fernet=None,
        url: str = "https://nz.ua/schedule/diary",
        params: dict | None = None,
):
    def save_current_cookies(previous_raw_cookies: str | None = None):
        if user_id is not None and db and fernet:
            raw_cookies = _dump_cookies(scraper)
            if previous_raw_cookies and _session_signature(previous_raw_cookies) == _session_signature(raw_cookies):
                return

            encrypted_cookies = fernet.encrypt(raw_cookies.encode()).decode()
            db.set_session_cookies(user_id, "nz", encrypted_cookies)

    if user_id is not None and db and fernet:
        encrypted = db.get_session_cookies(user_id, "nz")
        if encrypted:
            try:
                raw_cookies = fernet.decrypt(encrypted.encode()).decode()
                _load_cookies(scraper, raw_cookies)
                resp = scraper.get(url, params=params, timeout=10)
                soup = BeautifulSoup(resp.text, "html.parser")
                if _session_looks_authorized(soup):
                    save_current_cookies(raw_cookies)
                    _record_nz_event(db, "cookie_reuse")
                    logger.debug("Reused NZ session cookies for user_id=%s", user_id)
                    return resp, soup
                logger.debug("Stored NZ session cookies are expired for user_id=%s", user_id)
                _record_nz_event(db, "cookie_expired")
                db.delete_session_cookies(user_id, "nz")
            except Exception:
                logger.exception("Failed to reuse NZ session cookies for user_id=%s", user_id)
                _record_nz_event(db, "cookie_error")
                db.delete_session_cookies(user_id, "nz")

    logger.debug("Logging in to NZ for user_id=%s", user_id)
    _record_nz_event(db, "login")
    _login_nz(scraper, login, password)

    resp = scraper.get(url, params=params, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    if _session_looks_authorized(soup):
        save_current_cookies()
        logger.debug("Saved fresh NZ session cookies for user_id=%s", user_id)
    return resp, soup


@_dedupe_call
@_with_user_session_lock
def get_diary_schedule(
        login: str,
        password: str,
        days: list[str] | None = None,
        is_tiktok_mode: bool = False,
        user_id: int | None = None,
        db=None,
        fernet=None,
) -> str:
    scraper, cached_scraper = _get_scraper(user_id)
    _record_nz_event(db, "memory_hit" if cached_scraper else "memory_miss")
    try:
        _, soup = _open_authorized_page(
            scraper, user_id, login, password, db=db, fernet=fernet, url="https://nz.ua/schedule/diary"
        )

        soup = _ensure_current_semester(scraper, soup, f"{BASE}/schedule/diary")
        if soup is None:
            return "Не удалось получить CSRF токен."

        diary_items = soup.select(".diary-item")
        if days is None:
            days = ["сьогодні", "завтра"]
        schedule_by_day = {}

        def extract_date_text(txt):
            match = re.search(r'(\d{1,2}\s+[а-яіїє]+)', txt)
            return match.group(1) if match else ""

        def pick_best_link(box_element):
            btn = box_element.select_one("a.btn.btn-success")
            if btn: return btn.get("href")
            link_in_text = box_element.select_one(".diary-lesson-text a")
            if link_in_text:
                href = link_in_text.get("href")
                if href and "nz.ua" not in href: return href
            return None

        # --- ПАРСИНГ ---
        for item in diary_items:
            title_tag = item.select_one(".diary-item__title")
            if not title_tag: continue
            title_text = title_tag.get_text(strip=True).lower()

            if "сьогодні" in title_text or "сегодня" in title_text:
                base_name = "сьогодні"
            elif "завтра" in title_text:
                base_name = "завтра"
            else:
                base_name = title_text.split(",")[0].strip()

            if base_name not in days: continue

            date_part = extract_date_text(title_text)
            display_name = f"{base_name.capitalize()} — {date_part}" if date_part else base_name.capitalize()

            schedule_by_day[display_name] = []

            for box in item.select(".diary-box"):
                subject_tag = box.select_one(".diary-item__label")
                time_tag = box.select_one(".diary-item__time")

                time = " - ".join(t.strip() for t in time_tag.stripped_strings) if time_tag else None
                meet_link = pick_best_link(box)
                subject = subject_tag.get_text(strip=True) if subject_tag else "——"

                schedule_by_day[display_name].append({
                    "subject": subject,
                    "time": time,
                    "meet": meet_link
                })

        # --- БЛОК ЗАВТРА (ПАГІНАЦІЯ) ---
        if "завтра" in days:
            has_tomorrow = any(k.lower().startswith("завтра") for k in schedule_by_day.keys())
            if not has_tomorrow:
                next_link_tag = soup.select_one("a.pnl-next.diary-link")
                href = next_link_tag.get("href") if next_link_tag else None
                if href:
                    next_url = urljoin("https://nz.ua", href)
                    next_resp = scraper.get(next_url, timeout=10)
                    next_soup = BeautifulSoup(next_resp.text, "html.parser")
                    next_items = next_soup.select(".diary-item")
                    if next_items:
                        item = next_items[0]
                        title_tag = item.select_one(".diary-item__title")
                        display_name_next = "Завтра"
                        if title_tag:
                            next_text = title_tag.get_text(strip=True).lower()
                            date_part = extract_date_text(next_text)
                            if date_part: display_name_next = f"Завтра — {date_part}"

                        schedule_by_day[display_name_next] = []
                        for box in item.select(".diary-box"):
                            subject_tag = box.select_one(".diary-item__label")
                            time_tag = box.select_one(".diary-item__time")
                            time = " - ".join(t.strip() for t in time_tag.stripped_strings) if time_tag else None
                            meet_link = pick_best_link(box)
                            subject = subject_tag.get_text(strip=True) if subject_tag else "——"
                            schedule_by_day[display_name_next].append({
                                "subject": subject, "time": time, "meet": meet_link
                            })

        # --- ФОРМУВАННЯ ТЕКСТУ (З TIKTOK MODE) ---
        output = ""
        for day_header, lessons in schedule_by_day.items():
            output += f"📅 <b>{day_header}</b>\n"

            last_index = 0
            for idx, lesson in enumerate(lessons, start=1):
                if lesson["subject"] != "——":
                    last_index = idx
            visible_lessons = lessons[:last_index]
            if not visible_lessons: output += "<i>Уроків немає</i>\n"

            for i, lesson in enumerate(visible_lessons, start=1):
                output += f"{i}. "
                if lesson["time"]: output += f"<i>{lesson['time']}</i> "
                output += lesson["subject"]

                link = lesson['meet']
                if link:
                    # 🔥🔥🔥 TIKTOK LOGIC 🔥🔥🔥
                    if is_tiktok_mode:
                        # Если ссылка длинная, прячем последние 8 символов
                        if len(link) > 15:
                            safe_part = link[:-8]
                            hidden_part = link[-8:]
                            # Используем Telegram Spoiler
                            final_link_text = f"{safe_part}<tg-spoiler>{hidden_part}</tg-spoiler>"
                            output += f": {final_link_text}"
                        else:
                            # Если ссылка короткая, просто под спойлер всю
                            output += f": <tg-spoiler>{link}</tg-spoiler>"
                    else:
                        # Обычный режим
                        output += f": {link}"
                else:
                    if lesson['subject'] != "——":
                        output += ": —"
                output += "\n"
            output += "\n"

        if not output: return "Розклад не знайдено."
        return output
    except Exception as e:
        raise e
    finally:
        _release_scraper(user_id, scraper, keep=user_id is not None)


HW_RE = re.compile(r"Д\s*/\s*[зz]\s*:\s*(.*)", re.IGNORECASE | re.DOTALL)


def extract_homework(box) -> str | None:
    for p in box.select(".diary-lesson-row .diary-lesson-text p"):
        txt = p.get_text("\n", strip=True).replace("\xa0", " ").strip()

        m = HW_RE.search(txt)
        if not m:
            continue

        hw = (m.group(1) or "").strip()
        return hw if hw else None

    if box.select_one(".diary-add-red"):
        return "📌 ДЗ є у вкладці (натисни на предмет)"

    return None


def extract_hw_link(box) -> str | None:
    if not box.select_one(".diary-add-red"):
        return None

    a = box.select_one(".diary-add a[href]")
    if not a:
        return None

    href = a.get("href")
    if not href:
        return None

    if href.startswith("/"):
        return "https://nz.ua" + href

    return href


def _collect_homework(soup: BeautifulSoup, days: list[str]) -> dict[str, list[dict]]:
    """Парсить ДЗ зі сторінки щоденника у {день: [{subject, hw, link}, ...]}."""
    homework_by_day: dict[str, list[dict]] = {}

    for item in soup.select(".diary-item"):
        title_tag = item.select_one(".diary-item__title")
        if not title_tag:
            continue

        title_text = title_tag.get_text(" ", strip=True).lower()

        if "сьогодні" in title_text or "сегодня" in title_text:
            day_name = "сьогодні"
        elif "завтра" in title_text:
            day_name = "завтра"
        else:
            day_name = title_text.split(",")[0].strip()

        if day_name not in days:
            continue

        lessons = []

        for box in item.select(".diary-box"):
            subject_tag = box.select_one(".diary-item__label")
            subject = subject_tag.get_text(strip=True) if subject_tag else "——"

            hw_link = extract_hw_link(box)
            hw = extract_homework(box)
            if not hw:
                continue

            lessons.append({
                "subject": subject,
                "hw": hw,
                "link": hw_link
            })

        if lessons:
            homework_by_day[day_name] = lessons

    return homework_by_day


@_dedupe_call
@_with_user_session_lock
def get_diary_homework(
        login: str,
        password: str,
        days: list[str] | None = None,
        user_id: int | None = None,
        db=None,
        fernet=None,
) -> str:
    scraper, cached_scraper = _get_scraper(user_id)
    _record_nz_event(db, "memory_hit" if cached_scraper else "memory_miss")
    try:
        _, soup = _open_authorized_page(
            scraper, user_id, login, password, db=db, fernet=fernet, url="https://nz.ua/schedule/diary"
        )
        soup = _ensure_current_semester(scraper, soup, f"{BASE}/schedule/diary")
        if soup is None:
            return "Не удалось получить CSRF токен."

        if days is None:
            days = ["сьогодні"]

        homework_by_day = _collect_homework(soup, days)

        # ---- форматирование для Telegram (HTML) ----
        if not homework_by_day:
            return "✅ Д/з не знайдено."

        import html
        out = []
        for day, lessons in homework_by_day.items():
            out.append(f"📅 {html.escape(day).capitalize()}")
            for i, l in enumerate(lessons, start=1):
                subj = html.escape(l["subject"])
                hw = html.escape(l["hw"])
                if l.get("link"):
                    out.append(
                        f'<b>{i}. <a href="{html.escape(l["link"])}">{subj}:</a></b>'
                    )
                else:
                    out.append(f"<b>{i}. {subj}:</b>")
                out.append(f"<blockquote expandable>{hw}</blockquote>")
            out.append("")

        return "\n".join(out).strip()
    except Exception as e:
        raise e
    finally:
        _release_scraper(user_id, scraper, keep=user_id is not None)


@_dedupe_call
@_with_user_session_lock
def get_diary_grades(
        login: str,
        password: str,
        days_back: int = None,
        user_id: int | None = None,
        db=None,
        fernet=None,
) -> tuple[dict[str, float | None], str]:
    scraper, cached_scraper = _get_scraper(user_id)
    _record_nz_event(db, "memory_hit" if cached_scraper else "memory_miss")
    try:
        # --- 1. ПОЛУЧАЕМ СПИСОК СЕМЕСТРОВ ---
        grades_url = "https://nz.ua/schedule/grades-statement"

        _, soup = _open_authorized_page(
            scraper, user_id, login, password, db=db, fernet=fernet, url=grades_url
        )
        soup = _ensure_current_semester(scraper, soup, grades_url)
        if soup is None:
            return {}, "Не удалось получить CSRF токен."

        # Ищем выпадающий список семестров
        select_box = soup.find("select", {"id": "personalselectform-semester_id"})
        if not select_box:
            return {}, "Не удалось найти список семестров."

        # Находим текущий выбранный семестр (чтобы понять текущий год)
        selected_option = select_box.find("option", selected=True)
        if not selected_option:
            # Если ничего не выбрано, берем первый попавшийся как референс
            selected_option = select_box.find("option")

        # Парсим текст, например: "2025-2026 [1], ..." -> Нам нужно "2025-2026"
        current_option_text = selected_option.get_text()
        year_match = re.search(r"(\d{4}-\d{4})", current_option_text)

        if not year_match:
            return {}, "Не удалось определить текущий учебный год."

        current_year_str = year_match.group(1)  # "2025-2026"

        # Собираем ID всех семестров, которые относятся к ЭТОМУ году
        # Например, найдем ID для "2025-2026 [1]" и "2025-2026 [2]"
        target_semester_ids = []
        for option in select_box.find_all("option"):
            if current_year_str in option.get_text():
                target_semester_ids.append(option["value"])

        # Если мы не нашли семестров (странно), берем хотя бы текущий
        if not target_semester_ids:
            target_semester_ids.append(selected_option["value"])

        # --- 3. СБОР ОЦЕНОК ПО ВСЕМ СЕМЕСТРАМ ---

        # Словарь для хранения ВСЕХ оценок: {"Математика": [10, 11, 9], "Физика": [8]}
        all_grades_data = defaultdict(list)

        # Ищем CSRF токен конкретно для формы смены семестра (он может отличаться)
        semester_form = soup.find("form", {"id": "semester-select-form"})
        semester_csrf = _extract_csrf(soup, scraper)
        if semester_form:
            csrf_input = semester_form.find("input", {"name": "_csrf"})
            if csrf_input:
                semester_csrf = csrf_input["value"]
        if not semester_csrf:
            return {}, "Не удалось получить CSRF токен."

        original_semester_id = _selected_semester_id(select_box)

        try:
            # Проходимся по каждому семестру года (1-й и 2-й)
            for sem_id in target_semester_ids:
                _change_semester(scraper, semester_csrf, sem_id)

                # Загружаем страницу оценок для этого семестра.
                params = {}
                if days_back is not None:
                    now = datetime.datetime.now()
                    date_to_str = now.strftime("%Y-%m-%d")
                    date_from_str = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
                    params = {"date_from": date_from_str, "date_to": date_to_str}

                diary_resp = scraper.get(grades_url, params=params, timeout=10)
                sem_soup = BeautifulSoup(diary_resp.text, "html.parser")

                table = sem_soup.select_one("table.marks-report tbody")
                if not table:
                    continue

                for tr in table.select("tr"):
                    tds = tr.find_all("td")
                    if len(tds) < 3:
                        continue

                    subj = tds[1].get_text(strip=True)
                    results_text = tds[2].get_text(" ", strip=True)

                    nums = re.findall(r"\b\d{1,2}\b", results_text)
                    nums = [int(n) for n in nums]

                    if nums:
                        all_grades_data[subj].extend(nums)
        finally:
            if original_semester_id:
                try:
                    _change_semester(scraper, semester_csrf, original_semester_id)
                except Exception:
                    logger.exception("Failed to restore NZ semester after grades fetch")

        # --- 4. РАСЧЕТ ИТОГОВОГО СРЕДНЕГО ---

        final_averages = {}
        lines = []

        # Сортируем предметы по алфавиту для красоты
        for subj in sorted(all_grades_data.keys()):
            grades_list = all_grades_data[subj]
            if grades_list:
                avg = round(sum(grades_list) / len(grades_list), 2)
                final_averages[subj] = avg
                lines.append(f"{subj}: {avg} ({len(grades_list)} оцінок)")
            else:
                final_averages[subj] = None
                lines.append(f"{subj}: — (нема оцінок)")

        # Заголовок
        header = f"🎓 <b>Річна статистика ({current_year_str})</b>\n"
        if days_back:
            header += f"📅 За останні {days_back} днів\n"
        header += "\n📊 Середній бал (Семестр 1 + 2):\n\n"

        formatted = header + "\n".join(lines)

        return final_averages, formatted

    except Exception as e:
        logger.exception("Failed to get NZ grades")
        return {}, f"Помилка отримання даних: {e}"
    finally:
        _release_scraper(user_id, scraper, keep=user_id is not None)


@_dedupe_call
@_with_user_session_lock
def get_grade_events(
        login: str,
        password: str,
        limit: int = 20,
        user_id: int | None = None,
        db=None,
        fernet=None,
) -> list[dict]:
    """
    Возвращает список событий-оценок из /dashboard/news
    [{'name','date','text','hash'}, ...] (свежие сверху)
    """
    scraper, cached_scraper = _get_scraper(user_id)
    _record_nz_event(db, "memory_hit" if cached_scraper else "memory_miss")
    try:
        _, soup = _open_authorized_page(
            scraper, user_id, login, password, db=db, fernet=fernet, url="https://nz.ua/dashboard/news"
        )

        root = soup.find("div", id="school-news-list")
        if not root:
            return []

        items = root.select("div.news-page__item")
        if not items:
            return []

        out = []
        for item in items[:limit]:
            name_el = item.select_one(".news-page__header .news-page__name")
            date_el = item.select_one(".news-page__header .news-page__date")
            desc_el = item.select_one(".news-page__desc")

            name = name_el.get_text(strip=True) if name_el else "—"
            date = date_el.get_text(strip=True) if date_el else ""
            text = ""
            if desc_el:
                for br in desc_el.find_all("br"):
                    br.replace_with("\n")
                text = desc_el.get_text(" ", strip=True)

            # ЖЕСТКИЙ фильтр под твой пример
            t = text.lower()
            if "ви отримали оцінку" not in t:
                continue
            if "дистанц" in t or "дистанцій" in t or "завдання" in t:
                continue

            raw = f"{name}|{text}".encode("utf-8", "ignore")
            h = hashlib.sha1(raw).hexdigest()

            out.append({"name": name, "text": text, "hash": h})

        return out
    except Exception as e:
        raise e
    finally:
        _release_scraper(user_id, scraper, keep=user_id is not None)


@_dedupe_call
@_with_user_session_lock
def get_homework_events(
        login: str,
        password: str,
        days: list[str] | None = None,
        user_id: int | None = None,
        db=None,
        fernet=None,
) -> list[dict]:
    """
    ДЗ у вигляді подій з хешами — для сповіщень про НОВЕ ДЗ.
    [{'day','subject','hw','link','hash'}, ...]
    Хеш не залежить від дня-мітки («сьогодні» стає «завтра» наступного дня),
    інакше те саме ДЗ прилітало б двічі.
    """
    scraper, cached_scraper = _get_scraper(user_id)
    _record_nz_event(db, "memory_hit" if cached_scraper else "memory_miss")
    try:
        _, soup = _open_authorized_page(
            scraper, user_id, login, password, db=db, fernet=fernet, url="https://nz.ua/schedule/diary"
        )
        soup = _ensure_current_semester(scraper, soup, f"{BASE}/schedule/diary")
        if soup is None:
            return []

        if days is None:
            days = ["сьогодні", "завтра"]

        out = []
        for day_name, lessons in _collect_homework(soup, days).items():
            for lesson in lessons:
                out.append({
                    "day": day_name,
                    "subject": lesson["subject"],
                    "hw": lesson["hw"],
                    "link": lesson.get("link"),
                    "hash": homework_hash(lesson["subject"], lesson["hw"]),
                })
        return out
    except Exception as e:
        raise e
    finally:
        _release_scraper(user_id, scraper, keep=user_id is not None)


# diarynz.py

@_dedupe_call
@_with_user_session_lock
def get_diary_news(
        login: str,
        password: str,
        limit: int = 10,
        user_id: int | None = None,
        db=None,
        fernet=None,
) -> str:
    scraper, cached_scraper = _get_scraper(user_id)
    _record_nz_event(db, "memory_hit" if cached_scraper else "memory_miss")
    try:
        _, soup = _open_authorized_page(
            scraper, user_id, login, password, db=db, fernet=fernet, url="https://nz.ua/dashboard/news"
        )

        root = soup.find("div", id="school-news-list")
        if not root:
            return "Новини не знайдені."

        items = root.select("div.news-page__item")
        if not items:
            return "Новини не знайдені."

        out_lines = []
        base = "https://nz.ua"

        for item in items[:limit]:
            name_el = item.select_one(".news-page__header .news-page__name")
            date_el = item.select_one(".news-page__header .news-page__date")
            desc_el = item.select_one(".news-page__desc")

            name = name_el.get_text(strip=True) if name_el else "—"
            date = date_el.get_text(strip=True) if date_el else ""

            # Описание
            text = ""
            link_html = ""
            if desc_el:
                for br in desc_el.find_all("br"):
                    br.replace_with("\n")
                text = desc_el.get_text(" ", strip=True)

                # Ищем ссылку
                link_tag = desc_el.find("a", href=True)
                if link_tag:
                    link = urljoin(base, link_tag["href"])
                    # делаем слово "Дистанційне завдання" кликабельным
                    text = text.replace(
                        "Дистанційне завдання",
                        f'<a href="{link}">Дистанційне завдання</a>'
                    )

            out_lines.append(f"• <b>{name}</b> — {date}\n{text}".strip())

        return "\n\n".join(out_lines)
    except Exception as e:
        raise e
    finally:
        _release_scraper(user_id, scraper, keep=user_id is not None)
