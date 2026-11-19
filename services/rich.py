"""Rich-повідомлення Telegram (Bot API 10.1+): заголовки, таблиці, згортання.

Чисті функції: структуровані дані → рядок для InputRichMessage(html=...).
Без мережі, БД і бота — тестуються ізольовано.

На відміну від звичайного parse_mode=HTML, тут діє семантика справжнього
HTML: одиночний перенос рядка — це пробіл. Тому рядки ріжемо явно через
<p>/<br>, а все, що прийшло з сайту, обов'язково екрануємо.

Звичайний HTML-текст (classic) нікуди не дівається: його досі їдять фонові
сповіщення, дайджест і ШІ, і він же — запасний варіант, якщо Telegram
відхилить rich-повідомлення (див. utils.answer_rich).
"""
from collections import Counter
from html import escape

EMPTY_SUBJECT = "——"  # так NZ позначає «вікно» в розкладі


def esc(value) -> str:
    return escape(str(value), quote=True)


def multiline(value) -> str:
    """Текст із сайту → безпечний HTML зі збереженими переносами."""
    return "<br>".join(esc(line) for line in str(value).splitlines())


def link(url: str, label: str) -> str:
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def table(header: list[str] | None, rows: list[list[str]], attrs: str = "striped compact") -> str:
    """Комірки вже мають бути HTML (екрановані): тут лише каркас."""
    head = "<tr>" + "".join(f"<th>{cell}</th>" for cell in header) + "</tr>" if header else ""
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table {attrs}>{head}{body}</table>"


# ===== Оцінки: математика =====

def rounded_grade(grades: list[int]) -> int:
    """Табельна оцінка: середнє, округлене до цілого (.5 — вгору)."""
    return (2 * sum(grades) + len(grades)) // (2 * len(grades))


def needed_twelves(grades: list[int], target: int) -> int:
    """Скільки ще дванадцяток треба, щоб табельна оцінка стала target."""
    if not grades:
        return 1

    total = sum(grades)
    count = len(grades)
    target_threshold = 2 * target - 1  # target - 0.5, rounded half up
    difference = target_threshold * count - 2 * total
    if difference <= 0:
        return 0

    denominator = 24 - target_threshold  # 2 * (12 - target + 0.5)
    return (difference + denominator - 1) // denominator


def average(grades: list[int]) -> float:
    return round(sum(grades) / len(grades), 2)


def grade_dot(avg: float) -> str:
    """Рівні НУШ за 12-бальною шкалою: високий / достатній / середній / початковий."""
    if avg >= 10:
        return "🟢"
    if avg >= 7:
        return "🟡"
    if avg >= 4:
        return "🟠"
    return "🔴"


def grade_counts(grades: list[int]) -> str:
    """12×3 · 11×1 — від найвищої оцінки до найнижчої."""
    counts = Counter(grades)
    return " · ".join(f"{g}×{counts[g]}" for g in sorted(counts, reverse=True))


def plural_new_grades(n: int) -> str:
    """1 нова оцінка · 3 нові оцінки · 5 нових оцінок."""
    if n % 10 == 1 and n % 100 != 11:
        return "нова оцінка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "нові оцінки"
    return "нових оцінок"


def next_goal(grades: list[int]) -> str:
    current = rounded_grade(grades)
    if current >= 12:
        return "🏆 максимум"
    target = current + 1
    return f"12×{needed_twelves(grades, target)} → {target}"


# ===== Розклад =====

def visible_lessons(lessons: list[dict]) -> list[dict]:
    """Без «вікон» у кінці дня: NZ дописує порожні слоти до кінця сітки."""
    last = 0
    for idx, lesson in enumerate(lessons, start=1):
        if lesson["subject"] != EMPTY_SUBJECT:
            last = idx
    return lessons[:last]


def _lesson_cell(lesson: dict, is_tiktok_mode: bool) -> str:
    subject = lesson["subject"]
    if subject == EMPTY_SUBJECT:
        return "<i>вікно</i>"
    url = lesson.get("meet")
    if not url:
        return esc(subject)
    if is_tiktok_mode:
        # адмінський режим для відео: посилання видно, хвіст під спойлером
        head, tail = (url[:-8], url[-8:]) if len(url) > 15 else ("", url)
        return f"{esc(subject)}: {esc(head)}<tg-spoiler>{esc(tail)}</tg-spoiler>"
    return f"{link(url, subject)} 🔗"


def schedule(schedule_by_day: dict[str, list[dict]], is_tiktok_mode: bool = False) -> str:
    if not schedule_by_day:
        return "<p>📭 Розклад не знайдено.</p>"

    parts = []
    has_links = False
    for day, lessons in schedule_by_day.items():
        parts.append(f"<h3>📅 {esc(day)}</h3>")
        visible = visible_lessons(lessons)
        if not visible:
            parts.append("<p><i>Уроків немає — відпочивай 🙌</i></p>")
            continue
        rows = []
        for i, lesson in enumerate(visible, start=1):
            has_links = has_links or bool(lesson.get("meet"))
            time = (lesson.get("time") or "").replace(" - ", "–")
            rows.append([f"<b>{i}</b>", esc(time) or "—", _lesson_cell(lesson, is_tiktok_mode)])
        parts.append(table(["№", "Час", "Урок"], rows))

    if has_links and not is_tiktok_mode:
        parts.append("<footer>🔗 Натисни на урок, щоб приєднатися до онлайн-заняття</footer>")
    return "".join(parts)


# ===== ДЗ =====

def homework(homework_by_day: dict[str, list[dict]]) -> str:
    if not homework_by_day:
        return "<h3>📕 Домашнє завдання</h3><p>✅ ДЗ не знайдено — вільний вечір!</p>"

    parts = []
    for day, lessons in homework_by_day.items():
        parts.append(f"<h3>📕 ДЗ · {esc(day.capitalize())}</h3>")
        for i, lesson in enumerate(lessons, start=1):
            subject = lesson["subject"]
            title = link(lesson["link"], subject) if lesson.get("link") else esc(subject)
            parts.append(f"<p><b>{i}. {title}</b></p>")
            parts.append(f"<blockquote expandable>{multiline(lesson['hw'])}</blockquote>")
    return "".join(parts)


# ===== Новини =====

def news(items: list[dict]) -> str:
    if not items:
        return "<h3>📰 Новини школи</h3><p>📭 Новин поки немає.</p>"

    parts = ["<h3>📰 Новини школи</h3>"]
    for i, item in enumerate(items):
        if i:
            parts.append("<hr/>")
        date = f" · <i>{esc(item['date'])}</i>" if item.get("date") else ""
        parts.append(f"<p><b>{esc(item['name'])}</b>{date}</p>")
        text = item.get("text") or ""
        if text:
            body = multiline(text)
            if item.get("link"):
                body += f"<br>{link(item['link'], '📎 Відкрити завдання')}"
            parts.append(f"<blockquote expandable>{body}</blockquote>")
        elif item.get("link"):
            parts.append(f"<p>{link(item['link'], '📎 Відкрити завдання')}</p>")
    return "".join(parts)


# ===== Оцінки =====

def grades_summary(subjects: dict[str, dict]) -> dict | None:
    """Загальний середній (зважений кількістю оцінок), найкращий і найслабший предмет."""
    rated = {s: d["all"] for s, d in subjects.items() if d["all"]}
    if not rated:
        return None
    all_grades = [g for grades in rated.values() for g in grades]
    ranked = sorted(rated, key=lambda s: average(rated[s]), reverse=True)
    best, worst = ranked[0], ranked[-1]
    return {
        "overall": average(all_grades),
        "best": (best, average(rated[best])),
        "worst": (worst, average(rated[worst])),
    }


def grades(data: dict, is_vip: bool) -> str:
    """data — результат diarynz.get_grades_data()."""
    subjects = data["subjects"]
    parts = [f"<h3>🎓 Оцінки · {esc(data['year'])}</h3>"]
    if data.get("days_back"):
        parts.append(f"<p>📅 За останні {int(data['days_back'])} днів</p>")

    summary = grades_summary(subjects)
    if not summary:
        parts.append("<p>📭 <b>Оцінок поки немає</b> — вчителі ще нічого не виставили.</p>")
        return "".join(parts)

    if is_vip:
        best, best_avg = summary["best"]
        worst, worst_avg = summary["worst"]
        parts.append("<h4>📈 Твій прогноз</h4>")
        parts.append(table(
            None,
            [
                ["Загальний середній", f"<b>{summary['overall']}</b>"],
                ["💪 Найкращий", f"{esc(best)} — <b>{best_avg}</b>"],
                ["🧗 Найскладніший", f"{esc(worst)} — <b>{worst_avg}</b>"],
            ],
            attrs="bordered compact",
        ))
        parts.append("<p><i>Якщо збережеш темп — такий середній бал буде в кінці семестру.</i></p>")

    rows = []
    for subj in sorted(subjects):
        grades_list = subjects[subj]["all"]
        if not grades_list:
            continue
        avg = average(grades_list)
        rows.append([f"{grade_dot(avg)} {esc(subj)}", f"<b>{avg}</b>", str(len(grades_list))])
    parts.append("<h4>📊 Середній бал за рік</h4>")
    parts.append(table(["Предмет", "Бал", "Оцінок"], rows))

    parts.append("<h4>🧾 Усі оцінки</h4>")
    for subj in sorted(subjects):
        grades_list = subjects[subj]["all"]
        if not grades_list:
            continue
        sem_rows = [
            [esc(label), grade_counts(g), f"<b>{average(g)}</b>", next_goal(g)]
            for label, g in subjects[subj]["by_semester"] if g
        ]
        parts.append(
            f"<details><summary>{grade_dot(average(grades_list))} {esc(subj)} — "
            f"<b>{average(grades_list)}</b></summary>"
            + table(["Семестр", "Оцінки", "Середній", "🎯 Ціль"], sem_rows)
            + "</details>"
        )

    parts.append(
        "<footer>🟢 10–12 · 🟡 7–9 · 🟠 4–6 · 🔴 1–3<br>"
        "🎯 12×2 → 9 — дві дванадцятки підтягнуть табельну до 9</footer>"
    )
    return "".join(parts)


# ===== Загальне =====

def time_tag(unix: int, fmt: str, fallback: str) -> str:
    """Дата, яку клієнт покаже в мові й часовому поясі користувача.
    fmt: r — «через 3 дні», d / D — коротка / довга дата, t — час."""
    return f'<tg-time unix="{int(unix)}" format="{fmt}">{esc(fallback)}</tg-time>'


def from_classic(text: str) -> str:
    """Звичайний HTML бота → rich: абзаци через порожній рядок, переноси — <br>.
    Інлайн-теги (b, i, code, a) в rich ті самі, тож їх не чіпаємо."""
    blocks = [b.strip("\n") for b in text.split("\n\n") if b.strip()]
    return "".join(f"<p>{b.replace(chr(10), '<br>')}</p>" for b in blocks)


def sections(title: str, classic_sections: list[str], footer: str = "") -> str:
    """Довгий текст з розділами (політика) → заголовок + згортання.
    Перший рядок кожного розділу — назва, решта — вміст."""
    parts = [f"<h3>{title}</h3>"]
    for section in classic_sections:
        head, _, body = section.strip("\n").partition("\n")
        parts.append(f"<details><summary>{head}</summary>{from_classic(body)}</details>")
    if footer:
        parts.append(f"<footer>{footer}</footer>")
    return "".join(parts)


# ===== VIP =====

YES, NO = "✅", "—"

VIP_FEATURES = [
    # (функція, без VIP, з VIP)
    ("📅 Розклад і ДЗ на сьогодні", YES, YES),
    ("📆 Розклад на будь-який день", NO, YES),
    ("🔔 Нові оцінки — одразу в чат", NO, YES),
    ("📕 Нове ДЗ — одразу в чат", NO, YES),
    ("⏰ Нагадування за 5 хв до уроку", NO, YES),
    ("🌅 Ранковий дайджест о 7:30", "пн", "щодня"),
    ("📈 Прогноз і діаграма оцінок", NO, YES),
    ("🎨 Ексклюзивні теми Wrapped", NO, YES),
]


def vip_offer(*, user_id: int, bot_username: str, invites_left: int, reward_invites: int,
              total_invites: int, free_ai_limit: int, plans: list[tuple[str, int, int]]) -> str:
    """VIP-меню для того, у кого VIP немає. plans: [(назва, stars, грн), ...]."""
    features = [[esc(name), free, vip] for name, free, vip in VIP_FEATURES]
    features.insert(6, ["✨ ШІ-помічник", f"{free_ai_limit}/тиж", "1M токенів"])
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    plan_rows = [[esc(name), f"<b>{stars}</b> ⭐️", f"{uah} грн"] for name, stars, uah in plans]
    return (
        "<h2>⭐️ VIP</h2>"
        "<p>Без VIP бот відповідає, коли спитаєш. З VIP — <b>сам пише</b>, "
        "щойно з'явилась оцінка, ДЗ чи урок.</p>"
        + table(["", "Free", "VIP"], features, attrs="bordered striped compact")
        + "<h4>💎 Тарифи</h4>"
        + table(["Термін", "Stars", "Monobank"], plan_rows)
        + "<p><i>Stars — кнопки нижче, активація миттєва.</i></p>"
        + vip_offer_parents_line()
        + "<h4>🎁 Безкоштовно</h4>"
        "<ul>"
        f"<li><b>{reward_invites} друг → 3 дні VIP</b> (до 9 днів на місяць). "
        f"Прогрес: {invites_left}/{reward_invites}, всього запрошено: {total_invites}</li>"
        "</ul>"
        f"<p>Твоє посилання: <code>{esc(ref_link)}</code></p>"
        "<details><summary>🐈 Оплата через Monobank</summary>"
        "<p>Реквізити банки: <code>4874 1000 2294 2034</code><br>"
        f"{link('https://send.monobank.ua/jar/3bXsmYAcTp', '🔗 Відкрити Банку')}</p>"
        f"<p>⚠️ <b>Обов'язково</b> встав у коментар свій ID: <code>{user_id}</code><br>"
        "Без нього платіж не знайти — VIP вмикається вручну.</p>"
        "</details>"
    )


VIP_COMMANDS = [
    ("/diary", "розклад на будь-який день — кнопки під розкладом"),
    ("/notify_grades", "сповіщення про нові оцінки"),
    ("/notify_homework", "сповіщення про нове ДЗ"),
    ("/notify", "нагадування перед уроками"),
    ("/notify_digest", "ранковий дайджест щодня о 7:30"),
    ("/avg_grades", "прогноз, рейтинг предметів, діаграма"),
    ("/ai", "ШІ-помічник"),
    ("/wrapped", "ексклюзивні теми звіту"),
    ("/leaderboard", "топ амбасадорів"),
]


def vip_status(*, user_id: int, bot_username: str, expires: int, expires_text: str,
               invites_left: int, reward_invites: int, total_invites: int) -> str:
    if expires == 0:
        until = "<p>Діє <b>назавжди</b> 🙂</p>"
    else:
        until = (f"<p>Діє до <b>{time_tag(expires, 'D', expires_text)}</b> · "
                 f"{time_tag(expires, 'r', expires_text)}</p>")
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    return (
        "<h2>⭐️ VIP активний</h2>"
        + until
        + "<h4>Твої можливості</h4>"
        + table(None, [[cmd, esc(what)] for cmd, what in VIP_COMMANDS])
        + "<h4>🎁 +3 дні VIP за кожного друга</h4>"
        f"<p>Прогрес: <b>{invites_left}/{reward_invites}</b> · всього запрошено: <b>{total_invites}</b><br>"
        f"Твоє посилання: <code>{esc(ref_link)}</code></p>"
    )


# ===== Довідка =====

HELP_SECTIONS = [
    ("📚 Навчання", [
        ("/diary", "розклад уроків", False),
        ("/homework", "ДЗ та порада ШІ", False),
        ("/avg_grades", "оцінки та аналітика", False),
        ("/news", "шкільні новини", False),
    ]),
    ("🔔 Сповіщення", [
        ("/notify", "нагадування перед уроками", True),
        ("/notify_grades", "нові оцінки", True),
        ("/notify_homework", "нове ДЗ", True),
        ("/notify_digest", "ранковий дайджест (без VIP — по понеділках)", False),
    ]),
    ("✨ Інструменти", [
        ("/ai", "чат з ШІ", False),
        ("/wrapped", "підсумки тижня картинкою", False),
        ("/leaderboard", "топ амбасадорів", False),
    ]),
    ("⚙️ Акаунт", [
        ("/vip", "статус, тарифи, бонуси", False),
        ("/parents", "підключити батьків до оцінок", False),
        ("/support", "написати розробнику", False),
        ("/policy", "правила", False),
        ("/logout", "вийти", False),
    ]),
]


def help_page(is_vip: bool) -> str:
    parts = ["<h3>ℹ️ Довідка</h3>"]
    for title, commands in HELP_SECTIONS:
        parts.append(f"<h4>{title}</h4>")
        parts.append(table(None, [
            [cmd, esc(what) + (" ⭐️" if vip_only else "")]
            for cmd, what, vip_only in commands
        ]))
    footer = "⭐️ — тільки для VIP"
    if not is_vip:
        footer += "<br>VIP безкоштовно за друга — /vip"
    parts.append(f"<footer>{footer}</footer>")
    return "".join(parts)


# ===== Батьківський режим =====

def parent_invite(*, invite_link: str, parents: list[tuple[int, str, str]]) -> str:
    """Меню дитини: посилання для батьків і хто вже підключений."""
    parts = [
        "<h3>👨‍👩‍👧 Для батьків</h3>"
        "<p>Підключи маму чи тата — і твої <b>нові оцінки</b> приходитимуть їм у Telegram.</p>"
        "<ul>"
        "<li>👀 Вони бачать тільки оцінки. Пароль від щоденника не передається</li>"
        "<li>⭐️ Можуть оплатити тобі VIP: сповіщення, нагадування, ШІ без лімітів</li>"
        "<li>❌ Відключити можна будь-коли тут же</li>"
        "</ul>"
        f"<p>Посилання одноразове, діє 7 днів:<br><code>{esc(invite_link)}</code></p>"
    ]
    if parents:
        parts.append("<h4>Вже підключені</h4>")
        parts.append("<ul>" + "".join(
            f"<li>{esc(name or 'Без імені')}</li>" for _, name, _ in parents
        ) + "</ul>")
    return "".join(parts)


def parent_status(*, child_name: str, expires: int | None, expires_text: str) -> str:
    """Що бачать батьки по одній дитині. expires: None — VIP немає, 0 — назавжди."""
    name = esc(child_name or "дитина")
    parts = [f"<h3>👨‍👩‍👧 {name}</h3>"]
    if expires is None:
        parts.append(
            "<p>⏸ Сповіщення <b>на паузі</b> — у дитини немає VIP.</p>"
            "<p>Оформіть VIP, і кожна нова оцінка приходитиме вам і дитині, "
            "щойно вчитель її поставить. Плюс дитині — нагадування про онлайн-уроки "
            "і ШІ-помічник для домашки.</p>"
        )
    elif expires == 0:
        parts.append("<p>✅ Сповіщення про нові оцінки <b>увімкнені</b> назавжди.</p>")
    else:
        parts.append(
            f"<p>✅ Сповіщення про нові оцінки <b>увімкнені</b> до "
            f"<b>{time_tag(expires, 'D', expires_text)}</b> · {time_tag(expires, 'r', expires_text)}</p>"
        )
    parts.append("<footer>Ви бачите лише оцінки. Пароль від щоденника вам не передається.</footer>")
    return "".join(parts)


def parent_welcome(*, child_name: str) -> str:
    return (
        "<h2>👋 Вітаємо!</h2>"
        f"<p>Ви підключилися до щоденника <b>{esc(child_name or 'дитини')}</b> у NZ.ua.</p>"
        "<h4>Що ви отримуватимете</h4>"
        "<ul>"
        "<li>🔔 кожну нову оцінку — одразу, як вчитель її поставить</li>"
        "<li>⏸ повідомлення, якщо VIP дитини закінчиться і сповіщення стануть на паузу</li>"
        "</ul>"
        "<p>Більше нічого: ні реклами, ні розсилок.</p>"
    )


def vip_offer_parents_line() -> str:
    return ("<p>👨‍👩‍👧 <b>Немає чим платити?</b> Підключи батьків — "
            "вони отримуватимуть твої оцінки і зможуть оплатити VIP. Кнопка нижче.</p>")
