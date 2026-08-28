import unittest

from textutils import split_message, TELEGRAM_LIMIT


class SplitMessageTests(unittest.TestCase):
    def test_short_text_stays_whole(self):
        self.assertEqual(split_message("привіт"), ["привіт"])

    def test_empty_text(self):
        self.assertEqual(split_message(""), [""])

    def test_exactly_at_limit_is_not_split(self):
        text = "a" * TELEGRAM_LIMIT
        self.assertEqual(len(split_message(text)), 1)

    def test_every_chunk_within_limit(self):
        text = "\n\n".join(f"Абзац {i}: " + "я" * 300 for i in range(60))
        parts = split_message(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), TELEGRAM_LIMIT)

    def test_nothing_is_lost(self):
        blocks = [f"Розділ {i}\n" + "текст " * 100 for i in range(40)]
        text = "\n\n".join(blocks)
        joined = "".join(split_message(text))
        # символи збережені (окрім розділювачів між частинами)
        self.assertEqual(joined.replace("\n", ""), text.replace("\n", ""))

    def test_paragraph_longer_than_limit_is_split_by_lines(self):
        text = "\n".join("рядок " * 20 for _ in range(400))
        parts = split_message(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), TELEGRAM_LIMIT)

    def test_single_line_longer_than_limit_is_hard_cut(self):
        parts = split_message("x" * (TELEGRAM_LIMIT * 2 + 50))
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), TELEGRAM_LIMIT)

    def test_html_tags_stay_inside_one_chunk(self):
        """Теги в цьому проєкті закриваються в межах абзацу,
        тому після нарізки кількість відкритих і закритих <b> збігається."""
        block = "<b>Заголовок</b>\n" + "текст " * 200
        text = "\n\n".join(block for _ in range(20))
        for part in split_message(text):
            self.assertEqual(part.count("<b>"), part.count("</b>"))


class PolicyLengthTests(unittest.TestCase):
    def test_policy_is_chunked_under_limit(self):
        """Реальна політика довша за 4096 — саме на цьому падав бот."""
        import ast
        import pathlib

        def static_text(node):
            """Текст константи, навіть коли це f-рядок.

            Підставлені значення замінюємо зразком, а не порожнечею: тест
            міряє довжину того, що побачить користувач, і назва моделі в
            політиці — частина цієї довжини."""
            if isinstance(node, ast.JoinedStr):
                return "".join(
                    part.value if isinstance(part, ast.Constant) else "x" * 40
                    for part in node.values
                )
            return ast.literal_eval(node)

        source = pathlib.Path("handlers/common.py").read_text(encoding="utf-8")
        policy = None
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "policy_text":
                policy = static_text(node.value)
                break

        self.assertIsNotNone(policy, "policy_text не знайдено")
        for part in split_message(policy):
            self.assertLessEqual(len(part), TELEGRAM_LIMIT)


if __name__ == "__main__":
    unittest.main()
