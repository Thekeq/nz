import time
import re
from loader import ADMIN_ID, RATE_LOCK, USER_LAST_CALL, db, bot

DEFAULT_COOLDOWN = 5


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
    except Exception as e:
        print("activity error:", e)


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


async def process_referral_reward(user_id: int):
    if not db.try_mark_ref_rewarded(user_id):
        return

    referrer_id = db.get_referrer_for_reward(user_id)
    if not referrer_id or referrer_id == user_id:
        return

    count = db.add_invite_and_get(referrer_id, 1)

    need_invite = 1

    if count >= need_invite and db.try_consume_invites(referrer_id, need_invite):
        # 1. Додаємо days_to_add днів VIP
        days_to_add = 3
        db.set_vip(referrer_id, days_to_add)

        # 2. Нараховуємо токени
        MAX_TOKENS = 1000000
        REWARD = 50000

        current_tokens = db.get_tokens(referrer_id)
        new_balance = min(current_tokens + REWARD, MAX_TOKENS)
        db.set_tokens(referrer_id, new_balance)

        # 3. Повідомляємо
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 <b>Вітаємо! Твій друг приєднався!</b>\n\n"
                f"⭐️ VIP продовжено на <b>{days_to_add} дні(в)</b>\n"
                f"💎 Нараховано <b>{REWARD}</b> ШІ токенів\n"
                f"💰 Ваш баланс: <b>{compact_num(new_balance)}/{compact_num(MAX_TOKENS)}</b> токенів",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        try:
            await bot.send_message(
                referrer_id,
                f"Ви запросили друга ✅\nПрогрес: {count}/{need_invite} до безкоштовного ВІП ⭐️"
            )
        except Exception:
            pass


def compact_num(num):
    if num >= 1_000_000:
        val = round(num / 1_000_000, 1)  # Округляємо до 1 знаку (1.5M)
        return f"{val}M".replace(".0M", "M")  # Прибираємо .0, якщо число ціле
    elif num >= 1_000:
        val = round(num / 1_000, 1)
        return f"{val}K".replace(".0K", "K")
    return str(num)
