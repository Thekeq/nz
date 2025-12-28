from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import datetime
import uuid
import pytz
from loader import HW_AI_CACHE
from utils import clean_html

KYIV_TZ = pytz.timezone("Europe/Kiev")
CANONICAL_DAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця"]

kb_provider = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟦 Нові Знання (NZ.ua)")],
        [KeyboardButton(text="🟪 Human")],
    ],
    resize_keyboard=True
)

kb_retry = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="♻️ Спробувати увійти знову", callback_data="retry_login")]
    ]
)


def build_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Розклад"),
                KeyboardButton(text="📕 Д/з")
            ],
            [
                KeyboardButton(text="⭐️ Free VIP"),
                KeyboardButton(text="📊 Статистика"),
                KeyboardButton(text="📰 Новини")
            ],
            [
                KeyboardButton(text="ℹ️ Головне меню")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Обери дію…"
    )


def build_vip_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Розклад"),
                KeyboardButton(text="📕 Д/з")
            ],
            [
                KeyboardButton(text="✨ ШІ"),
                KeyboardButton(text="📊 Статистика"),
                KeyboardButton(text="📰 Новини"),
            ],
            [
                KeyboardButton(text="ℹ️ Головне меню")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Обери дію…"
    )


def share_kb(user_id: int):
    text = (
        "📚 Я користуюсь ботом для NZ.ua\n"
        "Він показує розклад, оцінки та надсилає нагадування перед уроками.\n"
        "Зручно і швидко 👌\n\n"
        f"https://t.me/nzdiary_bot?start={user_id}"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Поділитись у чат",
                    switch_inline_query=text
                )
            ]
        ]
    )


def payment_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Отримати VIP за 75 ⭐️", pay=True)
    return builder.as_markup()


def get_styles_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="Default", callback_data="wstyle:default")
    builder.button(text="⭐️ Luxury", callback_data="wstyle:gold")
    builder.button(text="👨‍💻 Matrix", callback_data="wstyle:matrix")
    builder.button(text="🌅 Vibe", callback_data="wstyle:sunset")
    builder.button(text="🌊 Deep Blue", callback_data="wstyle:ocean")
    builder.adjust(2)  # Кнопки в 2 стовпчики
    return builder.as_markup()


async def keyboard_diary(provider):
    now = datetime.datetime.now(KYIV_TZ)
    weekday = now.weekday()  # 0=Пн ... 6=Нд

    # визначаємо індекс "сьогодні" тільки якщо це навчальний день
    today_idx = weekday if weekday < 5 else None

    rows = []
    row = []

    for i, canon_day in enumerate(CANONICAL_DAYS):
        if today_idx is not None and i == today_idx:
            text = "Сьогодні"
            if provider == "human":
                cb_day = canon_day
            else:
                cb_day = "сьогодні"
        elif today_idx is not None and i == today_idx + 1:
            text = "Завтра"
            if provider == "human":
                cb_day = canon_day
            else:
                cb_day = "завтра"
        else:
            text = canon_day.capitalize()
            cb_day = canon_day

        row.append(
            InlineKeyboardButton(
                text=text,
                callback_data=f"diary_day:{cb_day}"
            )
        )

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    return keyboard


async def keyboard_hw(provider):
    if provider == "human":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📘 Сьогодні",
                        callback_data="diary_hw:today"
                    ),
                    InlineKeyboardButton(
                        text="📙 Завтра",
                        callback_data="diary_hw:tomorrow"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📚 Тиждень",
                        callback_data="diary_hw:week"
                    )
                ]
            ]
        )
        return keyboard

    now = datetime.datetime.now(KYIV_TZ)
    weekday = now.weekday()  # 0=Пн ... 6=Нд

    # визначаємо індекс "сьогодні" тільки якщо це навчальний день
    today_idx = weekday if weekday < 5 else None

    rows = []
    row = []

    for i, canon_day in enumerate(CANONICAL_DAYS):
        if today_idx is not None and i == today_idx:
            text = "Сьогодні"
            cb_day = "сьогодні"
        elif today_idx is not None and i == today_idx + 1:
            text = "Завтра"
            cb_day = "завтра"
        else:
            text = canon_day.capitalize()
            cb_day = canon_day

        row.append(
            InlineKeyboardButton(
                text=text,
                callback_data=f"diary_hw:{cb_day}"
            )
        )

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    return keyboard


def add_ai_button(kb: InlineKeyboardMarkup, text_content: str) -> InlineKeyboardMarkup:
    """Додає кнопку ШІ до існуючої клавіатури, зберігаючи текст у кеш."""
    # 1. Очищаємо текст і зберігаємо в кеш
    clean_text = clean_html(text_content)
    if clean_text == "✅ Д/з не знайдено.":
        return kb
    temp_id = str(uuid.uuid4())[:8]
    HW_AI_CACHE[temp_id] = clean_text

    # 2. Створюємо кнопку
    ai_btn = InlineKeyboardButton(text="✨ Порада від ШІ", callback_data=f"ai_hw:{temp_id}")

    # 3. Додаємо кнопку новим рядком до існуючої клавіатури
    # kb.inline_keyboard - це список списків [[btn, btn], [btn]]
    # Ми робимо копію, щоб не ламати оригінал, якщо він десь кешується
    new_kb_list = list(kb.inline_keyboard)
    new_kb_list.append([ai_btn])

    return InlineKeyboardMarkup(inline_keyboard=new_kb_list)
