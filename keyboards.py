from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import datetime
import uuid
import pytz
import texts
from loader import BOT_USERNAME, HW_AI_CACHE
from utils import clean_html
from urllib.parse import quote

KYIV_TZ = pytz.timezone("Europe/Kiev")
CANONICAL_DAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця"]


def _share_url(user_id: int, description: str) -> str:
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    return f"https://t.me/share/url?url={quote(ref_link)}&text={quote(description)}"


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
                KeyboardButton(text=texts.BTN_DIARY),
                KeyboardButton(text=texts.BTN_HOMEWORK),
                KeyboardButton(text=texts.BTN_GRADES),
            ],
            [
                KeyboardButton(text=texts.BTN_NEWS),
                KeyboardButton(text=texts.BTN_VIP_FREE),
                KeyboardButton(text=texts.BTN_HELP),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Обери дію…"
    )


def build_vip_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=texts.BTN_DIARY),
                KeyboardButton(text=texts.BTN_HOMEWORK),
                KeyboardButton(text=texts.BTN_GRADES),
            ],
            [
                KeyboardButton(text=texts.BTN_NEWS),
                KeyboardButton(text=texts.BTN_AI),
                KeyboardButton(text=texts.BTN_HELP),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Обери дію…"
    )


def share_kb(user_id: int):
    description = (
        "\n📱 Мій шкільний асистент у Telegram\n\n"
        "• Оцінки з NZ.ua та Human\n"
        "• Розклад уроків\n"
        "• Нагадування перед уроком\n\n"
        "Спробуй, це зручніше за сайт 👇"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Запросити друга",
                    url=_share_url(user_id, description)
                )
            ]
        ]
    )


def invite_friend_kb(user_id: int, label: str = "📤 Поділитися ботом"):
    description = (
        "Я користуюсь ботом для NZ.ua/Human: розклад, ДЗ, оцінки, новини і нагадування прямо в Telegram."
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, url=_share_url(user_id, description))]
        ]
    )


def result_actions_kb(user_id: int, base_kb: InlineKeyboardMarkup | None = None):
    description = (
        "Зручний бот для школи: розклад, ДЗ, оцінки, новини та нагадування з NZ.ua/Human прямо в Telegram."
    )
    rows = [list(row) for row in base_kb.inline_keyboard] if base_kb else []
    rows.append([
        InlineKeyboardButton(text="📤 Поділитися ботом", url=_share_url(user_id, description))
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_keyboard(stars: int = 75):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Сплатити {stars} ⭐️", pay=True)
    return builder.as_markup()


def vip_plans_kb(user_id: int):
    description = (
        "\n📱 Мій шкільний асистент у Telegram\n\n"
        "• Оцінки з NZ.ua та Human\n"
        "• Розклад уроків\n"
        "• Нагадування перед уроком\n\n"
        "Спробуй, це зручніше за сайт 👇"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🐣 1 тиждень — 25 ⭐️", callback_data="buy:week")],
            [InlineKeyboardButton(text="🔥 1 місяць — 75 ⭐️ (популярний)", callback_data="buy:month")],
            [InlineKeyboardButton(text="💎 3 місяці — 200 ⭐️ (вигідніше)", callback_data="buy:months3")],
            [InlineKeyboardButton(text="🎁 Безкоштовно: запросити друга", url=_share_url(user_id, description))],
            [InlineKeyboardButton(text="📣 +3 дні за підписку на канал", url="https://t.me/nzdiaryua")],
            [InlineKeyboardButton(text="✅ Я підписався", callback_data="check_sub")],
        ]
    )


def vip_upsell_kb():
    """Кнопка під пейволом: веде одразу до тарифів, а не в глухий кут."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Отримати VIP", callback_data="vip_menu")]
        ]
    )


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
    if clean_text in (texts.NO_HOMEWORK, "✅ Д/з не знайдено.", "📚 ДЗ на тиждень —"):
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
