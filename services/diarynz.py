import re
from collections import defaultdict
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


def get_diary_grades(login: str, password: str, days_back: int = None) -> tuple[dict[str, float | None], str]:
    scraper = cloudscraper.create_scraper()
    try:
        # --- 1. ЛОГИН ---
        r = scraper.get("https://nz.ua/")
        soup = BeautifulSoup(r.text, "html.parser")

        csrf_tag = soup.find("input", {"name": "_csrf"})
        if not csrf_tag:
            return {}, "Не удалось получить CSRF токен."
        csrf = csrf_tag["value"]

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
            return {}, "Неверный логин или пароль, либо капча."

        # --- 2. ПОЛУЧАЕМ СПИСОК СЕМЕСТРОВ ---
        grades_url = "https://nz.ua/schedule/grades-statement"

        # Делаем первый заход, чтобы получить список семестров
        initial_resp = scraper.get(grades_url)
        soup = BeautifulSoup(initial_resp.text, "html.parser")

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
        semester_csrf = csrf  # фоллбек
        if semester_form:
            csrf_input = semester_form.find("input", {"name": "_csrf"})
            if csrf_input:
                semester_csrf = csrf_input["value"]

        # Проходимся по каждому семестру года (1-й и 2-й)
        for sem_id in target_semester_ids:
            # А. Меняем семестр на сервере
            change_data = {
                '_csrf': semester_csrf,
                'semester_id': sem_id
            }
            # Важно: это POST запрос, он просто меняет состояние сессии
            scraper.post('https://nz.ua/site/semester-change', data=change_data)

            # Б. Загружаем страницу оценок для ЭТОГО семестра
            # Если нужны days_back, параметры добавляем, но для общего среднего лучше брать всё
            params = {}
            if days_back is not None:
                # Если пользователь хочет фильтр по датам, применяем его
                now = datetime.datetime.now()
                date_to_str = now.strftime("%Y-%m-%d")
                date_from_str = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
                params = {"date_from": date_from_str, "date_to": date_to_str}

            # Делаем GET запрос уже с новым семестром
            diary_resp = scraper.get(grades_url, params=params)
            sem_soup = BeautifulSoup(diary_resp.text, "html.parser")

            table = sem_soup.select_one("table.marks-report tbody")
            if not table:
                continue

            for tr in table.select("tr"):
                tds = tr.find_all("td")
                if len(tds) < 3:
                    continue

                # Имя предмета
                subj = tds[1].get_text(strip=True)
                # Строка оценок
                results_text = tds[2].get_text(" ", strip=True)

                # Парсим числа
                nums = re.findall(r"\b\d{1,2}\b", results_text)
                nums = [int(n) for n in nums]

                if nums:
                    all_grades_data[subj].extend(nums)

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
        print(f"Error: {e}")
        return {}, f"Помилка отримання даних: {e}"
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
