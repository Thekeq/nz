import re
from typing import Tuple, Dict, Any
from urllib.parse import urljoin

import cloudscraper
from bs4 import BeautifulSoup


def get_diary_schedule(login: str, password: str, days: list[str] | None = None) -> str:
    scraper = cloudscraper.create_scraper()

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
    resp = scraper.post("https://nz.ua/login", data=data)

    if resp.url != "https://nz.ua/":
        return "Не удалось войти. Проверьте логин и пароль."

    # 3. Получаем расписание
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

            subject = subject_tag.get_text(strip=True) if subject_tag else "——"
            meet_link = meet_tag["href"] if meet_tag else None

            schedule_by_day[day_name].append({
                "subject": subject,
                "meet": meet_link
            })

    # Формируем текст для Telegram
    output = ""
    for day, lessons in schedule_by_day.items():
        output += f"📅 {day}\n"

        last_index = 0
        for idx, lesson in enumerate(lessons, start=1):
            if lesson["subject"] != "——":
                last_index = idx

        for i, lesson in enumerate(lessons[:last_index], start=1):
            output += f"{i}. {lesson['subject']}"
            if lesson['meet']:
                output += f": {lesson['meet']}"
            output += "\n"

        output += "\n"

    return output


def get_diary_grades(login: str, password: str) -> str | tuple[dict[Any, Any], str] | tuple[
    dict[str, float | None], str]:
    scraper = cloudscraper.create_scraper()

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
        diary_resp = scraper.get("https://nz.ua/schedule/grades-statement")
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


def get_diary_news(login: str, password: str, limit: int = 10) -> str:
    scraper = cloudscraper.create_scraper()

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
