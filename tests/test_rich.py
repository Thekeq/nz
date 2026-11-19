import importlib.util
import unittest
from html.parser import HTMLParser

from services import rich

# Теги з розділу «Rich HTML style» Bot API, які ми використовуємо.
ALLOWED = {
    "h2", "h3", "h4", "p", "br", "b", "i", "u", "code", "a", "mark",
    "table", "tr", "th", "td", "ul", "li", "details", "summary",
    "blockquote", "footer", "hr", "tg-spoiler", "tg-time",
}
VOID = {"br", "hr"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"tag <{tag}> not allowed")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag not in ALLOWED:
            self.errors.append(f"tag <{tag}/> not allowed")

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}>, open: {self.stack}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)


def check(html: str) -> str:
    """Падає, якщо розмітка зламана; повертає видимий текст."""
    c = _Checker()
    c.feed(html)
    c.close()
    assert not c.errors, c.errors
    assert not c.stack, f"unclosed: {c.stack}"
    assert len(html) <= 32768, "rich limit"
    return "".join(c.text)


SCHEDULE = {
    "Сьогодні — 19 листопада": [
        {"subject": "——", "time": "08:30 - 09:15", "meet": None},
        {"subject": "Алгебра <тест>", "time": "09:25 - 10:10", "meet": "https://meet.google.com/a?x=1&y=2"},
        {"subject": "Фізика", "time": None, "meet": None},
        {"subject": "——", "time": "11:00 - 11:45", "meet": None},
    ],
    "Завтра": [{"subject": "——", "time": None, "meet": None}],
}

GRADES = {
    "year": "2026-2027",
    "days_back": None,
    "subjects": {
        "Алгебра": {"all": [12, 10, 11], "by_semester": [("1 семестр", [12, 10, 11]), ("2 семестр", [])]},
        "Хімія & біо": {"all": [3], "by_semester": [("1 семестр", [3]), ("2 семестр", [])]},
    },
}


class RichMarkupTests(unittest.TestCase):
    def test_schedule_is_valid_and_escaped(self):
        html = rich.schedule(SCHEDULE)
        text = check(html)
        self.assertIn("Алгебра <тест>", text)
        self.assertIn("&amp;y=2", html)          # URL у href екранований
        self.assertIn("Уроків немає", text)      # день з одними «вікнами»
        self.assertNotIn("11:00", text)          # хвостове «вікно» обрізане
        self.assertIn("немає уроку", text)       # а перше лишилось
        self.assertIn("09:25–10:10", text)

    def test_schedule_tiktok_hides_link_tail(self):
        html = rich.schedule(SCHEDULE, is_tiktok_mode=True)
        check(html)
        self.assertIn("<tg-spoiler>", html)
        self.assertNotIn("<footer>", html)

    def test_empty_inputs_render(self):
        for html in (rich.schedule({}), rich.homework({}), rich.news([])):
            self.assertTrue(check(html).strip())

    def test_homework_keeps_line_breaks(self):
        html = rich.homework({"сьогодні": [
            {"subject": "Укр. мова", "hw": "впр. 5\nвивчити <правило>", "link": "https://nz.ua/x"},
        ]})
        text = check(html)
        self.assertIn("впр. 5<br>вивчити &lt;правило&gt;", html)
        self.assertIn("Сьогодні", text)

    def test_news_escapes_school_text(self):
        html = rich.news([
            {"name": "Збори <батьків>", "date": "18.11", "text": "a < b", "link": "https://nz.ua/t"},
            {"name": "Друга", "date": "", "text": "", "link": None},
        ])
        text = check(html)
        self.assertIn("Збори <батьків>", text)
        self.assertEqual(html.count("<hr/>"), 1)

    def test_grades_valid_for_both_tiers(self):
        for is_vip in (False, True):
            text = check(rich.grades(GRADES, is_vip))
            self.assertIn("Хімія & біо", text)
            self.assertEqual("Твій прогноз" in text, is_vip)

    def test_grades_empty(self):
        text = check(rich.grades({**GRADES, "subjects": {}}, True))
        self.assertIn("Оцінок поки немає", text)

    def test_static_screens_are_valid(self):
        check(rich.help_page(False))
        check(rich.help_page(True))
        check(rich.vip_offer(user_id=1, bot_username="bot", invites_left=0, reward_invites=1,
                             total_invites=2, free_ai_limit=3,
                             plans=[("1 тиждень", 25, 25), ("1 місяць", 75, 75)]))
        check(rich.vip_status(user_id=1, bot_username="bot", expires=1_800_000_000,
                              expires_text="15.01.2027", invites_left=0, reward_invites=1,
                              total_invites=0))
        check(rich.vip_status(user_id=1, bot_username="bot", expires=0, expires_text="",
                              invites_left=0, reward_invites=1, total_invites=0))
        check(rich.sections("Політика", ["<b>1. Розділ</b>\nрядок 1\n• рядок 2"], footer="<i>x</i>"))


class GradeMathTests(unittest.TestCase):
    def test_rounded_grade_half_up(self):
        self.assertEqual(rich.rounded_grade([10, 11]), 11)   # 10.5 → 11
        self.assertEqual(rich.rounded_grade([10, 10, 11]), 10)

    def test_needed_twelves_reaches_target(self):
        for grades in ([7], [3, 4, 5], [9, 9, 10, 8]):
            target = rich.rounded_grade(grades) + 1
            n = rich.needed_twelves(grades, target)
            self.assertGreaterEqual(rich.rounded_grade(grades + [12] * n), target)
            if n:
                self.assertLess(rich.rounded_grade(grades + [12] * (n - 1)), target)

    def test_next_goal_at_max(self):
        self.assertIn("максимум", rich.next_goal([12, 12]))

    def test_grade_counts_descending(self):
        self.assertEqual(rich.grade_counts([10, 12, 10]), "12×1 · 10×2")


@unittest.skipIf(
    any(importlib.util.find_spec(m) is None for m in ("bs4", "cloudscraper", "requests")),
    "scraper deps not installed",
)
class ClassicFormatTests(unittest.TestCase):
    """Звичайний текст їдять сповіщення, дайджест і парсер графіка — формат не міняємо."""

    def test_classic_schedule(self):
        from services.diarynz import format_schedule
        text = format_schedule(SCHEDULE)
        self.assertIn("📅 <b>Сьогодні — 19 листопада</b>\n1. <i>08:30 - 09:15</i> ——\n", text)
        self.assertIn("<i>Уроків немає</i>", text)
        self.assertEqual(format_schedule({}), "Розклад не знайдено.")

    def test_classic_grades_lines_feed_the_chart_parser(self):
        from services.diarynz import format_grades
        averages, main, pages = format_grades(GRADES)
        self.assertEqual(averages["Алгебра"], 11.0)
        self.assertIn("Алгебра: 11.0 (3 оцінок)", main)
        self.assertEqual(len(pages), 1)
        self.assertEqual(format_grades({"error": "boom"}), ({}, "boom", []))

    def test_classic_news_escapes(self):
        from services.diarynz import format_news
        text = format_news([{"name": "A<b", "date": "1", "text": "Дистанційне завдання <x>",
                             "link": "https://nz.ua/t"}])
        self.assertIn("A&lt;b", text)
        self.assertIn('<a href="https://nz.ua/t">Дистанційне завдання</a> &lt;x&gt;', text)


class ParentScreensTests(unittest.TestCase):
    def test_parent_screens_are_valid(self):
        check(rich.parent_invite(invite_link="https://t.me/bot?start=p_x", parents=[]))
        text = check(rich.parent_invite(invite_link="https://t.me/bot?start=p_x",
                                        parents=[(2, "Мама <3", "Петро")]))
        self.assertIn("Мама <3", text)
        for expires in (None, 0, 1_800_000_000):
            check(rich.parent_status(child_name="Петро", expires=expires, expires_text="15.01.2027"))
        check(rich.parent_welcome(child_name="Петро"))
        self.assertIn("на паузі", check(rich.parent_status(child_name="", expires=None, expires_text="")))

    def test_plural_new_grades(self):
        cases = {1: "нова оцінка", 2: "нові оцінки", 4: "нові оцінки", 5: "нових оцінок",
                 11: "нових оцінок", 12: "нових оцінок", 21: "нова оцінка", 22: "нові оцінки"}
        for n, word in cases.items():
            self.assertEqual(rich.plural_new_grades(n), word, n)


if __name__ == "__main__":
    unittest.main()
