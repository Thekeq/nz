"""Єдиний словник назв фіч і підписів кнопок.

Раніше та сама фіча звалася по-різному в кнопці, команді й тексті
(«Д/з» / «ДЗ» / «Домашнє завдання»). Тут — одне джерело правди.

ВАЖЛИВО: reply-клавіатура кешується на клієнті Telegram, тому після
перейменування кнопки старі підписи ще довго приходять від юзерів.
Для кожної кнопки тримаємо набір {новий} | LEGACY_* і фільтруємо по ньому.
"""

# --- підписи кнопок ---
BTN_DIARY = "📅 Розклад"
BTN_HOMEWORK = "📕 ДЗ"
BTN_GRADES = "📊 Оцінки"
BTN_NEWS = "📰 Новини"
BTN_AI = "✨ ШІ"
BTN_VIP = "⭐️ VIP"
BTN_VIP_FREE = "⭐️ Free VIP"
BTN_HELP = "ℹ️ Довідка"
BTN_LOGIN = "🔑 Увійти у щоденник"
BTN_EXAMPLE = "🔍 Показати приклад"

# --- старі підписи, які досі можуть прилетіти ---
DIARY_LABELS = {BTN_DIARY}
HOMEWORK_LABELS = {BTN_HOMEWORK, "📕 Д/з"}
GRADES_LABELS = {BTN_GRADES, "📊 Статистика"}
NEWS_LABELS = {BTN_NEWS}
AI_LABELS = {BTN_AI}
VIP_LABELS = {BTN_VIP, BTN_VIP_FREE}
HELP_LABELS = {BTN_HELP, "ℹ️ Головне меню"}
NOTIFY_LESSONS_LABELS = {"⏰ Нагадування уроків"}
NOTIFY_GRADES_LABELS = {"🔔 Оцінки (сповіщення)"}
POLICY_LABELS = {"📖 Політика"}

# --- назви фіч у текстах ---
HOMEWORK_WORD = "ДЗ"
TOKENS_WORD = "ШІ-токени"

# джерело правди — services/digest.py, звідки цей рядок і повертається
from services.digest import NO_HOMEWORK  # noqa: E402,F401
