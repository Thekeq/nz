"""Форматування ранкового дайджесту.

Чисті функції без мережі, БД і бота — тому тестуються ізольовано.
Вся робота з I/O живе в services/background.py.
"""
import hashlib
import re
from html import escape

CONF_LINK_RE = re.compile(r"https://(?:meet\.google\.com|[\w.-]*zoom\.us)/[^\s<\"']+")
LESSON_ROW_RE = re.compile(r"\s*\d+\.\s*\S")


def homework_hash(subject: str, hw: str) -> str:
    """Ідентифікатор ДЗ для дедуплікації сповіщень.

    Свідомо НЕ включає мітку дня: «сьогодні» стає «завтра» наступного дня,
    і те саме ДЗ прилітало б двічі.
    """
    raw = f"{subject}|{hw}".encode("utf-8", "ignore")
    return hashlib.sha1(raw).hexdigest()


def has_lessons(schedule_text: str) -> bool:
    """Чи є в тексті розкладу хоч один урок (рядки «1. ...»)."""
    if not schedule_text:
        return False
    return any(LESSON_ROW_RE.match(line) for line in schedule_text.splitlines())


def first_conf_link(schedule_text: str) -> str | None:
    match = CONF_LINK_RE.search(schedule_text or "")
    return match.group(0) if match else None


def build_digest_text(schedule_text: str, homework_text: str, is_vip: bool) -> str:
    """Збирає ранковий дайджест: розклад + перше посилання + ДЗ (+ апсел для не-VIP)."""
    parts = ["🌅 <b>Доброго ранку! Ось твій день:</b>", "", "📅 <b>Розклад</b>", schedule_text.strip()]

    link = first_conf_link(schedule_text)
    if link:
        parts += ["", f"🔗 <b>Перший онлайн-урок:</b> {escape(link, quote=False)}"]

    hw = (homework_text or "").strip()
    if hw and "не знайдено" not in hw:
        parts += ["", "📕 <b>Домашнє завдання</b>", hw]

    if not is_vip:
        parts += [
            "",
            "⭐️ Такий дайджест <b>щоранку</b> — у VIP. Зараз ти отримуєш його раз на тиждень.",
        ]

    return "\n".join(parts)
