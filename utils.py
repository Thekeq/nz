import asyncio
import datetime
import time
import re
import logging
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from loader import ADMIN_ID, RATE_LOCK, USER_LAST_CALL, db, bot
from textutils import split_message, CAPTION_LIMIT

logger = logging.getLogger(__name__)
DEFAULT_COOLDOWN = 5


async def answer_long(message, text: str, reply_markup=None, **kwargs):
    """Відповідає текстом будь-якої довжини, ріжучи на частини.

    Клавіатуру чіпляємо лише до останньої частини, щоб кнопки
    не дублювались під кожним куском.
    """
    parts = split_message(text)
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        await message.answer(
            part,
            reply_markup=reply_markup if is_last else None,
            **kwargs
        )
    return len(parts)


async def safe_send(
        user_id: int,
        text: str,
        photo: str | None = None,
        **kwargs
) -> bool:
    """Надсилає повідомлення (або фото з підписом) з обробкою лімітів Telegram.

    Повертає True, якщо надіслано. При флуд-ліміті чекає і повторює один раз.
    Якщо юзер заблокував бота — позначає його в БД, і наступні розсилки
    та фонові перевірки його вже пропускають.
    """
    async def deliver():
        if photo:
            await bot.send_photo(user_id, photo=photo, caption=text, **kwargs)
        else:
            await bot.send_message(user_id, text, **kwargs)

    try:
        await deliver()
        return True
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await deliver()
            return True
        except Exception:
            logger.exception("Retry send failed for user_id=%s", user_id)
            return False
    except TelegramForbiddenError:
        try:
            db.mark_blocked(user_id)
            logger.info("User %s blocked the bot, excluded from broadcasts", user_id)
        except Exception:
            logger.exception("Failed to mark user_id=%s as blocked", user_id)
        return False
    except Exception:
        logger.exception("send_message failed for user_id=%s", user_id)
        return False


async def user_can_call(user_id: int, action: str, cooldown: int = DEFAULT_COOLDOWN) -> bool:
    if user_id == ADMIN_ID:
        return True

    now = time.time()
    key = (user_id, action)
    async with RATE_LOCK:
        last = USER_LAST_CALL.get(key, 0)
        if now - last < cooldown:
            return False
        USER_LAST_CALL[key] = now
    return True


def track_activity(user_id: int):
    try:
        db.add_activity(user_id)
    except Exception:
        logger.exception("Failed to track activity for user_id=%s", user_id)


def clean_html(raw_html: str) -> str:
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    cleantext = cleantext.replace('\n', ' ').strip()
    return re.sub(r'\s+', ' ', cleantext)


def fix_ai_response(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
    if len(text) > 0 and text[-1] not in ['.', '!', '?', '>', '”']:
        text += "..."
    if text.count("<code>") > text.count("</code>"): text += "</code>"
    if text.count("<pre>") > text.count("</pre>"): text += "</pre>"
    if text.count("<b>") > text.count("</b>"): text += "</b>"
    return text


REF_REWARD_DAYS = 3
REF_MONTHLY_CAP = 3  # максимум нагород на 30 днів (= 9 днів VIP)

# Акція до 1 вересня: перший запрошений друг дає 5 днів замість 3.
# Одноразово на юзера — далі звичайні 3 дні.
PROMO_UNTIL = datetime.date(2026, 9, 1)
PROMO_REWARD_DAYS = 5


def _reward_days(referrer_id: int) -> tuple[int, bool]:
    """(скільки днів дати, чи це акційна нагорода)."""
    if datetime.date.today() >= PROMO_UNTIL:
        return REF_REWARD_DAYS, False
    # акція діє лише на першу нагороду в житті юзера
    first_ever = db.count_recent_ref_grants(referrer_id, days=36500) == 0
    return (PROMO_REWARD_DAYS, True) if first_ever else (REF_REWARD_DAYS, False)


async def process_referral_reward(user_id: int):
    if not db.try_mark_ref_rewarded(user_id):
        return

    referrer_id = db.get_referrer_for_reward(user_id)
    if not referrer_id or referrer_id == user_id:
        return

    count = db.add_invite_and_get(referrer_id, 1)
    need_invite = 1

    if count < need_invite:
        await safe_send(
            referrer_id,
            f"Ви запросили друга ✅\nПрогрес: {count}/{need_invite} до безкоштовного ВІП ⭐️"
        )
        return

    # Місячний ліміт: без нього активний юзер отримує VIP безкоштовно
    # вічно і ніколи не купує. Запрошення при цьому не згорає.
    if db.count_recent_ref_grants(referrer_id, days=30) >= REF_MONTHLY_CAP:
        await safe_send(
            referrer_id,
            "🎉 Твій друг приєднався! Але ліміт безкоштовного VIP на цей місяць "
            f"вичерпано ({REF_MONTHLY_CAP * REF_REWARD_DAYS} днів).\n"
            "Ліміт відновиться наступного місяця, а безліміт вже зараз — у /vip 😉"
        )
        return

    if not db.try_consume_invites(referrer_id, need_invite):
        return

    days, is_promo = _reward_days(referrer_id)

    # Реферальний VIP — «лайт»: нагадування, сповіщення про оцінки,
    # розклад по днях. ШІ-токени та ексклюзивні теми — лише у платному.
    db.set_vip(referrer_id, days, source="ref")
    db.add_ref_grant(referrer_id)

    await safe_send(
        referrer_id,
        f"🎉 <b>Вітаємо! Твій друг приєднався!</b>\n\n"
        + (f"🔥 <b>Акція до 1 вересня:</b> перший друг = <b>{days} днів</b> VIP!\n"
           if is_promo else "")
        + f"⭐️ VIP продовжено на <b>{days} дні(в)</b>\n"
        f"⏰ Нагадування, 🔔 сповіщення про оцінки та 📅 розклад по днях — твої!\n"
        f"✨ ШІ-асистент без лімітів і 🎨 ексклюзивні теми — у платному /vip",
        parse_mode="HTML"
    )


def compact_num(num):
    if num >= 1_000_000:
        val = round(num / 1_000_000, 1)  # Округляємо до 1 знаку (1.5M)
        return f"{val}M".replace(".0M", "M")  # Прибираємо .0, якщо число ціле
    elif num >= 1_000:
        val = round(num / 1_000, 1)
        return f"{val}K".replace(".0K", "K")
    return str(num)
