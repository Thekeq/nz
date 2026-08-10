import importlib.util
import unittest

from services.digest import has_lessons, first_conf_link, build_digest_text, homework_hash

missing_deps = [
    name
    for name in ("requests", "cloudscraper", "bs4")
    if importlib.util.find_spec(name) is None
]

NZ_SCHEDULE = (
    "📅 Сьогодні — 8 вересня\n"
    "1. Українська мова: https://meet.google.com/aaa-bbb-ccc\n"
    "2. Фізика: —\n"
)

HUMAN_SCHEDULE = (
    "📅 <b>Сьогодні 08.09.2026</b>\n"
    "1. <i>08:00 - 08:45</i> Алгебра: —\n"
    "2. <i>09:00 - 09:45</i> Хімія: https://us02web.zoom.us/j/123456\n"
)

HOMEWORK_HTML = """
<div class="diary-item">
  <div class="diary-item__title">{day_title}</div>
  <div class="diary-box">
    <div class="diary-item__label">{subject}</div>
    <div class="diary-lesson-row">
      <div class="diary-lesson-text"><p>Д/з: {hw}</p></div>
    </div>
  </div>
</div>
"""


class HasLessonsTests(unittest.TestCase):
    def test_detects_numbered_lessons(self):
        self.assertTrue(has_lessons(NZ_SCHEDULE))
        self.assertTrue(has_lessons(HUMAN_SCHEDULE))

    def test_empty_day_is_not_lessons(self):
        self.assertFalse(has_lessons(""))
        self.assertFalse(has_lessons("📅 <b>Сьогодні 08.09.2026</b>\n—\n"))
        self.assertFalse(has_lessons("Не зрозумів, за які дні показати розклад 🙃"))


class FirstConfLinkTests(unittest.TestCase):
    def test_picks_meet_link(self):
        self.assertEqual(first_conf_link(NZ_SCHEDULE), "https://meet.google.com/aaa-bbb-ccc")

    def test_picks_zoom_link(self):
        self.assertEqual(first_conf_link(HUMAN_SCHEDULE), "https://us02web.zoom.us/j/123456")

    def test_no_link(self):
        self.assertIsNone(first_conf_link("1. Фізика: —"))

    def test_link_is_not_polluted_by_markup(self):
        text = '1. Фізика: <a href="https://meet.google.com/xyz-abcd-efg">тут</a>'
        self.assertEqual(first_conf_link(text), "https://meet.google.com/xyz-abcd-efg")


class BuildDigestTests(unittest.TestCase):
    def test_vip_digest_has_no_upsell(self):
        text = build_digest_text(NZ_SCHEDULE, "📅 Сьогодні\n<b>1. Фізика:</b>", is_vip=True)
        self.assertIn("Доброго ранку", text)
        self.assertIn("Українська мова", text)
        self.assertIn("Перший онлайн-урок", text)
        self.assertIn("Домашнє завдання", text)
        self.assertNotIn("VIP", text)

    def test_free_digest_has_upsell(self):
        text = build_digest_text(NZ_SCHEDULE, "", is_vip=False)
        self.assertIn("VIP", text)
        self.assertIn("раз на тиждень", text)

    def test_missing_homework_block_is_skipped(self):
        text = build_digest_text(NZ_SCHEDULE, "✅ Д/з не знайдено.", is_vip=True)
        self.assertNotIn("Домашнє завдання", text)

    def test_no_link_means_no_link_line(self):
        text = build_digest_text("1. Фізика: —", "", is_vip=True)
        self.assertNotIn("Перший онлайн-урок", text)


class HomeworkHashTests(unittest.TestCase):
    def test_same_homework_same_hash(self):
        self.assertEqual(homework_hash("Фізика", "Задача 5"), homework_hash("Фізика", "Задача 5"))

    def test_different_homework_different_hash(self):
        self.assertNotEqual(homework_hash("Фізика", "Задача 5"), homework_hash("Фізика", "Задача 6"))

    def test_different_subject_different_hash(self):
        self.assertNotEqual(homework_hash("Фізика", "Задача 5"), homework_hash("Хімія", "Задача 5"))


@unittest.skipIf(bool(missing_deps), f"missing dependencies: {', '.join(missing_deps)}")
class CollectHomeworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global BeautifulSoup, _collect_homework

        from bs4 import BeautifulSoup
        from services.diarynz import _collect_homework

    @staticmethod
    def _soup(subject: str, hw: str, day_title: str = "Сьогодні, 8 вересня"):
        return BeautifulSoup(
            HOMEWORK_HTML.format(day_title=day_title, subject=subject, hw=hw),
            "html.parser",
        )

    def test_parses_subject_and_homework(self):
        result = _collect_homework(self._soup("Геометрія", "§12, номери 3-7"), ["сьогодні"])
        self.assertIn("сьогодні", result)
        self.assertEqual(result["сьогодні"][0]["subject"], "Геометрія")
        self.assertEqual(result["сьогодні"][0]["hw"], "§12, номери 3-7")

    def test_day_not_requested_is_skipped(self):
        self.assertEqual(_collect_homework(self._soup("Геометрія", "§12"), ["завтра"]), {})

    def test_hash_stable_when_day_label_shifts(self):
        """Те саме ДЗ, що сьогодні було «сьогодні», завтра стане «завтра» —
        хеш мусить залишитись тим самим, інакше сповіщення продублюється."""
        today = _collect_homework(
            self._soup("Фізика", "Задача 5", "Сьогодні, 8 вересня"), ["сьогодні"]
        )["сьогодні"][0]
        tomorrow = _collect_homework(
            self._soup("Фізика", "Задача 5", "Завтра, 9 вересня"), ["завтра"]
        )["завтра"][0]

        self.assertEqual(
            homework_hash(today["subject"], today["hw"]),
            homework_hash(tomorrow["subject"], tomorrow["hw"]),
        )


if __name__ == "__main__":
    unittest.main()
