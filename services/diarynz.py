import re
from typing import Tuple, Dict, Any
from urllib.parse import urljoin
import hashlib
import cloudscraper
from bs4 import BeautifulSoup
import datetime


class InvalidCredentials(Exception):
    pass


LINK_PRIORITIES = [
    "meet.google.com",
    "zoom.us",
]

BASE = "https://nz.ua"


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


def get_diary_schedule(login: str, password: str, days: list[str] | None = None) -> str:
    scraper = cloudscraper.create_scraper()
    try:
        # 1. Получаем CSRF
        r = scraper.get("https://nz.ua/")
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_tag = soup.find("input", {"name": "_csrf"})
        if not csrf_tag:
            return "Не удалось получить CSRF токен."
        csrf = csrf_tag["value"]

        # 2. Логин
        data = {
            '_csrf': csrf,
            'LoginForm[login]': login,
            'LoginForm[password]': password,
            'LoginForm[rememberMe]': ['0', '1'],
            'ajax': 'login-form',
            'login-button': 'undefined',
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://nz.ua/",
            "Origin": "https://nz.ua",
        }

        resp_val = scraper.post("https://nz.ua/login", data=data, headers=headers)
        j = resp_val.json()  # тут уже будет JSON, раз ты добавил headers
        if j:
            msg = (j.get("loginform-password") or j.get("loginform-login") or ["Сталася невідома помилка"])[0]
            raise InvalidCredentials(msg)
        # 3. Получаем расписание
        resp = scraper.post("https://nz.ua/login", data=data)
        diary_resp = scraper.get("https://nz.ua/schedule/diary")
        soup = BeautifulSoup(diary_resp.text, "html.parser")
        diary_items = soup.select(".diary-item")
        # Если дни не переданы, по умолчанию берем "сьогодні" и "завтра"
        if days is None:
            days = ["сьогодні", "завтра"]

        schedule_by_day = {}

        for item in diary_items:
            title_tag = item.select_one(".diary-item__title")
            if not title_tag:
                continue
            title_text = title_tag.get_text(strip=True).lower()

            # Определяем день
            if "сьогодні" in title_text or "сегодня" in title_text:
                day_name = "сьогодні"
            elif "завтра" in title_text:
                day_name = "завтра"
            else:
                day_name = title_text.split(",")[0].strip()  # первый словесный день

            if day_name not in days:
                continue

            schedule_by_day[day_name] = []

            for box in item.select(".diary-box"):
                subject_tag = box.select_one(".diary-item__label")
                meet_tag = box.select_one(".diary-lesson-text a")
                time_tag = box.select_one(".diary-item__time")

                if time_tag:
                    # "09:00<br>09:45" → "09:00 - 09:45"
                    time = " - ".join(
                        t.strip() for t in time_tag.stripped_strings
                    )
                else:
                    time = None
                subject = subject_tag.get_text(strip=True) if subject_tag else "——"
                meet_link = pick_best_link(box)

                schedule_by_day[day_name].append({
                    "subject": subject,
                    "time": time,
                    "meet": meet_link
                })

            if "завтра" in days and "завтра" not in schedule_by_day:
                next_link_tag = soup.select_one("a.pnl-next.diary-link")
                href = next_link_tag.get("href") if next_link_tag else None

                if href:
                    next_url = urljoin("https://nz.ua", href)
                    next_resp = scraper.get(next_url)
                    next_soup = BeautifulSoup(next_resp.text, "html.parser")
                    next_items = next_soup.select(".diary-item")

                    if next_items:
                        # Берём перший день з наступного тижня і вважаємо його "завтра"
                        item = next_items[0]
                        schedule_by_day["завтра"] = []

                        for box in item.select(".diary-box"):
                            subject_tag = box.select_one(".diary-item__label")
                            meet_tag = box.select_one(".diary-lesson-text a")
                            time_tag = box.select_one(".diary-item__time")

                            if time_tag:
                                # "09:00<br>09:45" → "09:00 - 09:45"
                                time = " - ".join(
                                    t.strip() for t in time_tag.stripped_strings
                                )
                            else:
                                time = None
                            subject = subject_tag.get_text(strip=True) if subject_tag else "——"
                            meet_link = pick_best_link(box)

                            schedule_by_day["завтра"].append({
                                "subject": subject,
                                "time": time,
                                "meet": meet_link
                            })

        # Формируем текст для Telegram
        output = ""
        for day, lessons in schedule_by_day.items():
            output += f"📅 {day.capitalize()}\n"

            last_index = 0
            for idx, lesson in enumerate(lessons, start=1):
                if lesson["subject"] != "——":
                    last_index = idx

            for i, lesson in enumerate(lessons[:last_index], start=1):
                output += f"{i}. "
                if lesson["time"]:
                    output += f"<i>{lesson['time']}</i> "

                output += lesson["subject"]

                if lesson['meet']:
                    output += f": {lesson['meet']}"
                else:
                    if lesson['subject'] != "——":
                        output += ": —"

                output += "\n"

            output += "\n"

        return output
    except Exception as e:
        raise e
    finally:
        scraper.close()


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


def get_diary_homework(login: str, password: str, days: list[str] | None = None) -> str:
    scraper = cloudscraper.create_scraper()
    try:
        # 1) CSRF
        r = scraper.get("https://nz.ua/")
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_tag = soup.find("input", {"name": "_csrf"})
        if not csrf_tag:
            return "Не удалось получить CSRF токен."
        csrf = csrf_tag["value"]

        # 2) Логин (ajax-проверка как у тебя)
        data = {
            "_csrf": csrf,
            "LoginForm[login]": login,
            "LoginForm[password]": password,
            "LoginForm[rememberMe]": ["0", "1"],
            "ajax": "login-form",
            "login-button": "undefined",
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://nz.ua/",
            "Origin": "https://nz.ua",
        }

        resp_val = scraper.post("https://nz.ua/login", data=data, headers=headers)
        j = resp_val.json()
        if j:
            msg = (j.get("loginform-password") or j.get("loginform-login") or ["Сталася невідома помилка"])[0]
            raise InvalidCredentials(msg)

        # 3) Получаем дневник
        resp = scraper.post("https://nz.ua/login", data=data)
        diary_resp = scraper.get("https://nz.ua/schedule/diary")
        soup = BeautifulSoup(diary_resp.text, "html.parser")
        diary_items = soup.select(".diary-item")

        if days is None:
            days = "сьогодні"

        homework_by_day: dict[str, list[dict]] = {}

        for item in diary_items:
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
        scraper.close()


def get_diary_grades(login: str, password: str, days_back: int = None) -> str | tuple[dict[Any, Any], str] | tuple[
    dict[str, float | None], str]:
    scraper = cloudscraper.create_scraper()
    try:
        # 1. Загружаем главную страницу, чтобы достать CSRF
        r = scraper.get("https://nz.ua/")
        soup = BeautifulSoup(r.text, "html.parser")

        csrf_tag = soup.find("input", {"name": "_csrf"})
        if not csrf_tag:
            return "Не удалось получить CSRF токен."
        csrf = csrf_tag["value"]

        # 2. Формируем данные для входа
        data = {
            '_csrf': csrf,
            'LoginForm[login]': login,
            'LoginForm[password]': password,
            'LoginForm[rememberMe]': ['0', '1'],
            'ajax': 'login-form',
            'login-button': 'undefined',
        }

        login_url = "https://nz.ua/login"
        resp = scraper.post(login_url, data=data)

        if resp.url == "https://nz.ua/":
            # успешный логин
            grades_url = "https://nz.ua/schedule/grades-statement"
            params = {}

            if days_back is not None:
                now = datetime.datetime.now()
                # date_to = сьогодні
                date_to_str = now.strftime("%Y-%m-%d")
                # date_from = сьогодні мінус days_back
                date_from_str = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")

                params = {
                    "date_from": date_from_str,
                    "date_to": date_to_str
                }

            diary_resp = scraper.get(grades_url, params=params)
            soup = BeautifulSoup(diary_resp.text, "html.parser")

            def _find_date_input(name):
                inp = soup.find("input", {"name": name})
                if inp and inp.get("value"):
                    return inp.get("value").strip()
                # fallback по id
                inp = soup.find("input", {"id": name})
                if inp and inp.get("value"):
                    return inp.get("value").strip()
                return None

            date_from = _find_date_input("date_from") or _find_date_input("classselectform-date_from")
            date_to = _find_date_input("date_to") or _find_date_input("classselectform-date_to")

            if date_from and date_to:
                range_line = f"📅 Діапазон дат: {date_from} — {date_to}\n\n"
            elif date_from:
                range_line = f"📅 Дата початку: {date_from}\n\n"
            elif date_to:
                range_line = f"📅 Дата закінчення: {date_to}\n\n"
            else:
                range_line = ""

            table = soup.select_one("table.marks-report tbody")
            if not table:
                return {}, "Нічого не знайдено."

            averages = {}
            lines = []
            for tr in table.select("tr"):
                tds = tr.find_all("td")
                if len(tds) < 3:
                    continue
                subj = tds[1].get_text(strip=True)
                results_text = tds[2].get_text(" ", strip=True)  # "1, 8, Н, п/п" -> "1, 8, Н, п/п"

                # Найдём все целые числа в строке (1..99). Это достаёт "12" из "12 (Тест)"
                nums = re.findall(r"\b\d{1,2}\b", results_text)
                nums = [int(n) for n in nums]

                if nums:
                    avg = round(sum(nums) / len(nums), 2)
                    averages[subj] = avg
                    lines.append(f"{subj}: {avg} ({len(nums)} оцінок)")
                else:
                    averages[subj] = None
                    # можно вывести оригинальный results_text, если нужно
                    if results_text:
                        lines.append(f"{subj}: — (ненумерічні оцінки: {results_text})")
                    else:
                        lines.append(f"{subj}: — (нема оцінок)")

            # Форматированный текст для Telegram
            formatted = range_line + "📊 Середній бал по предметам:\n\n" + "\n".join(lines)
            return averages, formatted
    except Exception as e:
        raise e
    finally:
        scraper.close()


def get_grade_events(login: str, password: str, limit: int = 20) -> list[dict]:
    """
    Возвращает список событий-оценок из /dashboard/news
    [{'name','date','text','hash'}, ...] (свежие сверху)
    """
    scraper = cloudscraper.create_scraper()
    try:
        r = scraper.get("https://nz.ua/")
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_tag = soup.find("input", {"name": "_csrf"})
        if not csrf_tag:
            return []
        csrf = csrf_tag["value"]

        data = {
            "_csrf": csrf,
            "LoginForm[login]": login,
            "LoginForm[password]": password,
            "LoginForm[rememberMe]": ["0", "1"],
            "ajax": "login-form",
            "login-button": "undefined",
        }
        resp = scraper.post("https://nz.ua/login", data=data)
        if resp.url != "https://nz.ua/":
            return []

        news_resp = scraper.get("https://nz.ua/dashboard/news")
        soup = BeautifulSoup(news_resp.text, "html.parser")

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
        scraper.close()


# diarynz.py

def get_diary_news(login: str, password: str, limit: int = 10) -> str:
    scraper = cloudscraper.create_scraper()
    try:
        # 1) CSRF
        r = scraper.get("https://nz.ua/")
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_tag = soup.find("input", {"name": "_csrf"})
        if not csrf_tag:
            return "Не удалось получить CSRF токен."
        csrf = csrf_tag["value"]

        # 2) Логин
        data = {
            "_csrf": csrf,
            "LoginForm[login]": login,
            "LoginForm[password]": password,
            "LoginForm[rememberMe]": ["0", "1"],
            "ajax": "login-form",
            "login-button": "undefined",
        }
        resp = scraper.post("https://nz.ua/login", data=data)
        if resp.url != "https://nz.ua/":
            return "Не удалось войти. Проверьте логин и пароль."

        # 3) Страница новин
        news_resp = scraper.get("https://nz.ua/dashboard/news")
        soup = BeautifulSoup(news_resp.text, "html.parser")

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
        scraper.close()
