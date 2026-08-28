import asyncio
import time
import datetime
import random
import re
import gc
import logging
from html import escape

from typing import Union
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, BufferedInputFile, \
    InlineQueryResultCachedPhoto, InlineQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import texts
from loader import (db, bot, HW_AI_CACHE, WRAPPED_CACHE, fernet, SEMAPHORE, ADMIN_ID, BOT_USERNAME,
    COOKIE_API_TOKEN, COOKIE_SOURCE, COOKIE_VIP_DAYS)
from utils import track_activity, fix_ai_response, user_can_call, compact_num, answer_long
from keyboards import build_vip_kb, share_kb, payment_keyboard, get_styles_kb, vip_plans_kb, vip_upsell_kb
from states import AIStates, WrappedState
from services.ai import ai, AIUnavailable
from services.drawer import draw_wrapped
from services.diaryhuman import get_diary_grades_human
from services.diarynz import get_diary_grades

router = Router()
logger = logging.getLogger(__name__)

FREE_AI_LIMIT = 3  # безкоштовних AI-запитів на тиждень для не-VIP

VIP_PLANS = {
    "week":    {"days": 7,  "stars": 25,  "tokens": 250_000,   "title": "VIP на 1 тиждень", "label": "1 тиждень"},
    "month":   {"days": 30, "stars": 75,  "tokens": 1_000_000, "title": "VIP на 1 місяць",  "label": "1 місяць"},
    "months3": {"days": 90, "stars": 200, "tokens": 3_000_000, "title": "VIP на 3 місяці",  "label": "3 місяці"},
}
# Win-back: 48 годин після закінчення VIP — місяць зі знижкою
WINBACK_PLAN = {"days": 30, "stars": 50, "tokens": 1_000_000, "title": "VIP на 1 місяць (-33%)", "label": "1 місяць"}
WINBACK_GRACE_SEC = 48 * 3600


CHANNEL_ID = "@nzdiaryua"
CHANNEL_URL = "https://t.me/nzdiaryua"
CHANNEL_BONUS_DAYS = 3


@router.callback_query(F.data == "check_sub")
async def check_channel_sub(callback: CallbackQuery):
    user_id = callback.from_user.id

    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
    except Exception:
        # бот не адмін у каналі або канал недоступний — це наша проблема
        logger.exception("get_chat_member failed for %s in %s", user_id, CHANNEL_ID)
        await callback.answer("⚠️ Не вдалося перевірити підписку. Спробуй пізніше.", show_alert=True)
        return

    if member.status not in ("member", "administrator", "creator"):
        await callback.answer("❌ Підписки не видно. Підпишись і натисни ще раз.", show_alert=True)
        return

    if not db.try_use_channel_bonus(user_id):
        await callback.answer("Бонус за підписку вже отримано 🙂", show_alert=True)
        return

    db.set_vip(user_id, days=CHANNEL_BONUS_DAYS, source="ref")
    db.record_command_metric("funnel:channel_bonus", 0)

    await callback.answer()
    await callback.message.answer(
        f"🎁 <b>+{CHANNEL_BONUS_DAYS} дні VIP за підписку!</b>\n\n"
        "⏰ /notify — нагадування перед уроками\n"
        "🌅 /notify_digest — ранковий дайджест\n"
        "🔔 /notify_grades — сповіщення про оцінки",
        parse_mode="HTML",
        reply_markup=build_vip_kb()
    )


def _is_vip(user_id: int) -> bool:
    vip_flag, expires = db.get_vip_status(user_id)
    return bool(vip_flag) and (expires == 0 or expires > int(time.time()))


@router.message(Command('vip'))
@router.message(F.text.in_(texts.VIP_LABELS))
@router.callback_query(F.data == "vip_menu")
async def vip_func(event: Union[Message, CallbackQuery]):
    user_id = event.from_user.id

    # Логіка визначення об'єкта повідомлення
    if isinstance(event, CallbackQuery):
        message_object = event.message
        await event.answer()  # Прибрати анімацію завантаження
    else:
        message_object = event

    track_activity(user_id)
    # Дозволяємо відкривати VIP-меню навіть без кредів (оплата не залежить від NZ)
    db.ensure_user(user_id)

    vip_flag, expires = db.get_vip_status(user_id)
    invites_left, total_invites = db.get_invite_progress(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
    progress_text = f"{invites_left}/1 до наступних 3 днів VIP"
    # Обмін увімкнено тільки коли є токен: без нього нагороду ніхто не видасть,
    # і кнопка вела б у гру з обіцянкою, яку нема кому виконати
    cookie_claimed = (not COOKIE_API_TOKEN
                      or db.has_partner_grant(user_id, COOKIE_SOURCE))

    if is_vip:
        date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y")
        if expires == 0:
            date_str = "НАЗАВЖДИ :)"
        await message_object.answer(
            f"⭐️ Ви маєте VIP до <b>{date_str}</b>\n\n"
            f"🏆 /leaderboard - <b>Топ VIP Амбасадорів</b>\n"
            f"✨ /ai — <b>Використання ШІ</b>\n"
            f"👕 /wrapped — <b>Ексклюзивні теми (Matrix, Gold, Ocean)</b>\n"
            f"📅 /diary_days (/diary) — <b>Перегляд розкладу по дням</b>\n"
            f"📊 /avg_grades — <b>Розумний прогноз оцінок, рейтинг предметів, діаграма-павутинка</b>\n"
            f"🌅 /notify_digest — <b>Ранковий дайджест о 7:30 щодня</b>\n"
            f"⏰ /notify — <b>Нагадування перед уроками</b>\n"
            f"🔔 /notify_grades — <b>Сповіщення о нових оцінках (nz)</b>\n"
            f"📕 /notify_homework — <b>Сповіщення про нове ДЗ (nz)</b>\n"
            f"⚡ <b>Пріоритетні запити та підтримка</b>\n\n"
            f"<blockquote>🎁 <b>БЕЗКОШТОВНИЙ VIP</b> на 3 дні за кожного друга!\n"
            f"Прогрес: <b>{progress_text}</b>\n"
            f"Всього запрошено: <b>{total_invites}</b>\n"
            f"Ваше реферальне посилання:\n"
            f"https://t.me/{BOT_USERNAME}?start={user_id}</blockquote>",
            reply_markup=share_kb(user_id, cookie_claimed),
            parse_mode="HTML", disable_web_page_preview=True
        )
    else:
        db.record_command_metric("funnel:vip_menu", 0)
        cookie_line = "" if cookie_claimed else (
            f"🍪 <b>{COOKIE_VIP_DAYS} днів VIP безкоштовно</b> — просто зайди "
            "в гру Cookie Merge за кнопкою внизу. Нагорода прилетить сюди "
            "автоматично, платити нічого не треба.\n\n")
        await message_object.answer(
            f"{cookie_line}"
            "🎁 Безкоштовний VIP: 1 друг → 3 дні VIP (до 9 днів на місяць)\n"
            f"Прогрес: <b>{progress_text}</b>\n"
            f"Всього запрошено: <b>{total_invites}</b>\n"
            f"Ваше реферальне посилання: https://t.me/{BOT_USERNAME}?start={user_id}\n\n"
            f"⭐️ <b>Перелік VIP-Функцій:</b>\n"
            f"🌅 Ранковий дайджест о 7:30 <b>щодня</b> (без VIP — по понеділках)\n"
            f"🎨 Ексклюзивні теми (Matrix, Gold, Ocean)\n"
            f"✨ ШІ-асистент без тижневих лімітів\n"
            f"📅 Перегляд розкладу по дням\n"
            f"⏰ Нагадування за 5-хв до уроку\n"
            f"🔔 Сповіщення про нові оцінки (NZ)\n"
            f"📕 Сповіщення про нове ДЗ (NZ)\n"
            f"📊 Розширений рейтинг та статистика, діаграма-павутинка\n"
            f"⚡ Пріоритетні запити та швидка підтримка\n\n"
            f"💎 <b>Оберіть тариф</b> — оплата Telegram Stars, активація миттєва:\n\n"
            f"🐈 <i>Або Monobank (Банка): 25 / 75 / 200 грн — тиждень / місяць / 3 місяці</i>\n"
            f"<blockquote>Реквізити банки: <code>4874 1000 2294 2034</code>\n"
            f"🔗 <a href='https://send.monobank.ua/jar/3bXsmYAcTp'>Натисніть тут, щоб відкрити Банку</a>\n"
            f"⚠️ <b>ВАЖЛИВО!</b> У коментар до платежу вставте свій ID:\n"
            f"👉 <code>{user_id}</code> 👈 <i>(натисніть щоб скопіювати)</i></blockquote>",
            reply_markup=vip_plans_kb(user_id, cookie_claimed),
            disable_web_page_preview=True
        )


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery):
    plan_key = callback.data.split(":")[1]
    plan = VIP_PLANS.get(plan_key)
    if not plan:
        await callback.answer("⚠️ Невідомий тариф", show_alert=True)
        return

    user_id = callback.from_user.id
    db.record_command_metric(f"funnel:invoice:{plan_key}", 0)

    await callback.message.answer_invoice(
        title=plan["title"],
        description=f"Миттєвий доступ до всіх VIP-функцій на {plan['label']} 🚀",
        payload=f"vip:{plan_key}:{user_id}",
        provider_token="",  # токен від Telegram Payments (Stars)
        currency="XTR",
        prices=[LabeledPrice(label=plan["title"], amount=plan["stars"])],
        start_parameter="vip_payment",
        reply_markup=payment_keyboard(plan["stars"])
    )
    await callback.answer()


@router.callback_query(F.data == "buy_winback")
async def buy_winback(callback: CallbackQuery):
    user_id = callback.from_user.id
    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    active = bool(vip_flag) and (expires == 0 or expires > now_ts)

    # знижка діє лише 48 годин після закінчення VIP
    if active or not expires or now_ts > expires + WINBACK_GRACE_SEC:
        await callback.answer("⌛️ Ця пропозиція вже недійсна", show_alert=True)
        return

    db.record_command_metric("funnel:invoice:winback", 0)
    await callback.message.answer_invoice(
        title=WINBACK_PLAN["title"],
        description="Повернення VIP зі знижкою: всі функції на 1 місяць 🚀",
        payload=f"vip:winback:{user_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=WINBACK_PLAN["title"], amount=WINBACK_PLAN["stars"])],
        start_parameter="vip_payment",
        reply_markup=payment_keyboard(WINBACK_PLAN["stars"])
    )
    await callback.answer()


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    user_id = message.from_user.id

    payload = message.successful_payment.invoice_payload or ""
    parts = payload.split(":")
    plan_key = parts[1] if len(parts) >= 2 and parts[0] == "vip" else "month"
    plan = WINBACK_PLAN if plan_key == "winback" else VIP_PLANS.get(plan_key, VIP_PLANS["month"])

    db.set_vip(user_id, days=plan["days"], source="paid")
    # токени не зрізаємо, якщо в юзера залишилось більше
    db.set_tokens(user_id, max(db.get_tokens(user_id), plan["tokens"]))
    db.record_command_metric(f"funnel:paid:{plan_key}", 0)

    vip, expires = db.get_vip_status(user_id)
    date_str = "НАЗАВЖДИ :)" if expires == 0 else datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y %H:%M")
    await message.answer(
        f"✅ Ви отримали VIP доступ до {date_str}\n"
        f"💎 На балансі <b>{compact_num(db.get_tokens(user_id))} ШІ-токенів</b>!\n\n"
        f"🎁 За кожного запрошеного друга — ще +3 дні VIP:\n"
        f"https://t.me/{BOT_USERNAME}?start={user_id}",
        reply_markup=build_vip_kb(),
        disable_web_page_preview=True
    )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await pre_checkout_q.answer(ok=True)


@router.message(Command("ai"))
@router.message(F.text.in_(texts.AI_LABELS))
async def ai_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    exit_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вийти з режиму ШІ ✨", callback_data="exit_ai")]
        ]
    )

    if _is_vip(user_id):
        current_tokens = db.get_tokens(user_id)
        await state.set_state(AIStates.waiting_input)
        await message.reply(
            "✨ Напиши питання текстом або надішли фото з підписом\n"
            f"У вас ще 💎 <b>{compact_num(current_tokens)}/1M</b> токенів",
            reply_markup=exit_kb
        )
    else:
        # не-VIP: даємо спробувати — FREE_AI_LIMIT запитів на тиждень
        left = db.free_ai_left(user_id, FREE_AI_LIMIT)
        if left > 0:
            await state.set_state(AIStates.waiting_input)
            await message.reply(
                "✨ Напиши питання текстом або надішли фото з підписом\n"
                f"🎁 У тебе <b>{left}/{FREE_AI_LIMIT}</b> безкоштовних запитів на цьому тижні\n"
                "Безліміт з 1M токенів — у /vip",
                reply_markup=exit_kb
            )
        else:
            await message.reply(
                f"🎁 Безкоштовні ШІ-запити цього тижня закінчились ({FREE_AI_LIMIT}/{FREE_AI_LIMIT})\n"
                "⭐️ VIP дає <b>1,000,000 токенів</b> — вистачає на сотні запитів!",
                reply_markup=vip_upsell_kb()
            )


async def _charge_ai(message: Message, state: FSMContext, user_id: int, is_vip: bool) -> bool:
    """Списання за AI-запит: не-VIP витрачає тижневий безкоштовний ліміт."""
    if is_vip:
        return True
    if db.try_use_free_ai(user_id, FREE_AI_LIMIT):
        return True
    await message.reply(
        "🎁 Безкоштовні ШІ-запити цього тижня закінчились.\n"
        "⭐️ У VIP — <b>1,000,000 токенів</b> без тижневих лімітів!",
        reply_markup=vip_upsell_kb()
    )
    await state.clear()
    return False


_AI_ALERT_AT = 0.0
_AI_ALERT_COOLDOWN = 3600


async def _handle_ai_unavailable(user_id: int, is_vip: bool, error: Exception, answer_to):
    """ШІ впав не з вини користувача: повертаємо безкоштовний запит,
    кажемо правду замість «змініть запитання» і пінгуємо адміна."""
    global _AI_ALERT_AT

    if not is_vip:
        db.refund_free_ai(user_id)

    logger.error("AI unavailable for user_id=%s: %s", user_id, error)

    now = time.time()
    if now - _AI_ALERT_AT > _AI_ALERT_COOLDOWN:
        _AI_ALERT_AT = now
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ <b>ШІ недоступний</b>\n<code>{escape(str(error)[:600])}</code>",
                parse_mode="HTML"
            )
        except Exception:
            logger.exception("Failed to alert admin about AI outage")

    await answer_to(
        "🛠 ШІ тимчасово недоступний — це на нашому боці, не в твоєму запитанні.\n"
        "Спробуй трохи пізніше." + ("" if is_vip else "\n<i>Безкоштовний запит повернуто.</i>"),
        parse_mode="HTML"
    )


def _ai_cost_footer(user_id: int, is_vip: bool, cost: int) -> str:
    """VIP платить токенами, не-VIP бачить залишок безкоштовних запитів."""
    if is_vip:
        db.deduct_tokens(user_id, cost)
        return f"\n💎 Витрачено: {cost} токенів."
    left = db.free_ai_left(user_id, FREE_AI_LIMIT)
    return f"\n🎁 Безкоштовних запитів залишилось: {left}/{FREE_AI_LIMIT} (безліміт у /vip)"


@router.message(AIStates.waiting_input)
async def ai_input_text_or_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_vip = _is_vip(user_id)

    if is_vip:
        current_tokens = db.get_tokens(user_id)
        if current_tokens <= 0:
            await message.reply(
                "❌ У вас закінчилися ШІ-токени! Поновіть VIP, щоб отримати ще 1 млн.",
                reply_markup=vip_upsell_kb()
            )
            await state.clear()
            return

    if message.text:
        if len(message.text) > 400:
            await message.reply(f"Максимальна довжина тексту 400 символів. Ви ввели {len(message.text)}")
            return
        user_prompt = message.text.strip()
        if not await _charge_ai(message, state, user_id, is_vip):
            return
        try:
            # Викликаємо оновлену функцію, яка повертає ДВА значення
            answer, cost = await ai(user_prompt)
            if not answer:
                await message.reply("😫 Халепа... спробуйте трохи змінити запитання!")
                await state.clear()
                return
            answer = fix_ai_response(answer)
            answer += f"\n\n🔄 <i>Щоб задати нове питання — натисни</i> /ai"
            answer += _ai_cost_footer(user_id, is_vip, cost)

            await answer_long(message, answer, parse_mode="HTML", disable_web_page_preview=True)

        except AIUnavailable as e:
            await _handle_ai_unavailable(user_id, is_vip, e, message.reply)
        except Exception:
            logger.exception("AI text request failed for user_id=%s", user_id)
            await message.reply("😫 Халепа... спробуйте трохи змінити запитання!")

        await state.clear()
        return

    if message.photo:
        caption = (message.caption or "").strip()
        if len(caption) > 400:
            await message.reply(f"Максимальна довжина тексту 400 символів. Ви ввели {len(message.text)}")
            return
        if not caption:
            caption = "Опиши, що зображено на фото, і зроби короткий аналіз."

        # берём самое большое фото
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        img_bytes = file_bytes.read()

        if not await _charge_ai(message, state, user_id, is_vip):
            return
        try:
            # Викликаємо оновлену функцію з фото
            answer, cost = await ai(caption, img_bytes)
            if not answer:
                await message.reply("😫 Халепа... спробуйте трохи змінити запитання!")
                await state.clear()
                return
            answer = fix_ai_response(answer)
            answer += "\n\n🔄 <i>Щоб задати нове питання — натисни</i> /ai"
            answer += _ai_cost_footer(user_id, is_vip, cost)

            await answer_long(message, answer, parse_mode="HTML", disable_web_page_preview=True)

        except AIUnavailable as e:
            await _handle_ai_unavailable(user_id, is_vip, e, message.reply)
        except Exception:
            logger.exception("AI photo request failed for user_id=%s", user_id)
            await message.reply("😫 Халепа... спробуйте трохи змінити запитання!")

        await state.clear()
        return

    # если ни текста, ни фото
    await message.reply("Надішли текст або фото з підписом 🙂")


@router.callback_query(F.data.startswith("ai_hw:"))
async def handle_ai_homework(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_vip = _is_vip(user_id)

    temp_id = callback.data.split(":")[1]
    hw_text = HW_AI_CACHE.get(temp_id)

    if not hw_text:
        await callback.answer("⚠️ Дані застаріли, спробуйте ще раз. /homework")
        return

    if is_vip:
        current_tokens = db.get_tokens(user_id)
        if current_tokens <= 0:
            await callback.answer("❌ Закінчилися ШІ-токени!", show_alert=True)
            return
    else:
        # не-VIP: порада по ДЗ у рахунок тижневого безкоштовного ліміту
        if not db.try_use_free_ai(user_id, FREE_AI_LIMIT):
            await callback.message.answer(
                "🎁 Безкоштовні ШІ-запити цього тижня закінчились.\n"
                "⭐️ VIP дає <b>1,000,000 токенів</b> — порада по ДЗ щодня!",
                reply_markup=vip_upsell_kb()
            )
            await callback.answer()
            return

    await callback.message.edit_text("🤖 ШІ думає над порадою...")

    # Твій виклик AI
    system_instruction = (
        "Role: Educational Mentor & Study Strategist. Task: create a smart execution plan for homework.\n"
        "Structure your answer with these exact emojis and sections:\n"
        "📅 Пріоритети: (order of tasks and why, e.g., 'start with hard, finish with easy')\n"
        "💡 Лайфхак: (GIVE A SPECIFIC TACTIC FOR THIS HOMEWORK. Examples: 'Use Ctrl+F to find answers in the textbook', 'Write formulas on a draft first', 'For English, use a translator for context but type manually'. AVOID generic 'Pomodoro' or 'Feynman' unless it involves reading 10+ pages.)\n"
        "🔗 [Subject Name]: (briefly what to do with links/tasks)\n"
        "Style: 'Pro student' vibe, sharp, practical, ultra-concise. Use Ukrainian language."
    )
    full_prompt = f"{system_instruction}\n\nОсь моє ДЗ: {hw_text}"
    try:
        response_text, cost = await ai(full_prompt)
        if not response_text:
            await callback.message.answer("❌ Не вдалося отримати пораду. Спробуй пізніше.")
            return
        response_text += "\n" + _ai_cost_footer(user_id, is_vip, cost)

        await answer_long(callback.message, response_text, parse_mode="HTML")

    except AIUnavailable as e:
        await _handle_ai_unavailable(user_id, is_vip, e, callback.message.answer)
    except Exception:
        logger.exception("AI homework request failed for user_id=%s", user_id)
        await callback.message.answer("❌ Не вдалося отримати пораду. Спробуй пізніше.")
    # Видаляємо з кешу, щоб не забивати пам'ять
    HW_AI_CACHE.pop(temp_id, None)


@router.callback_query(F.data == "exit_ai")
async def ai_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("✨ Ви вийшли з режиму ШІ.")
    except Exception:
        await callback.message.answer("✨ Ви вийшли з режиму ШІ.")
    await callback.answer()


@router.message(Command("wrapped"))
async def send_wrapped(message: Message, state: FSMContext):
    user_id = message.from_user.id
    track_activity(user_id)

    # Антиспам
    allowed = await user_can_call(user_id, "wrapped", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом.")
        return

    if not db.has_credentials(user_id):
        await message.answer("Щоб почати, увійдіть у свій аккаунт\n/login")
        return

    wait_msg = await message.answer("🎨 Аналізую твій тиждень...")

    name = message.from_user.first_name

    try:
        login, enc_password, provider = db.get_user(user_id)
        password = fernet.decrypt(enc_password.encode()).decode()

        # 1. ОТРИМАННЯ ДАНИХ
        async with SEMAPHORE:
            days_back = datetime.datetime.now().weekday() + 1

            if provider == "human":
                text = await asyncio.to_thread(get_diary_grades_human, login, password, days_back)
                try:
                    avg = float(re.search(r'Середній:</b>\s*([\d.]+)', text).group(1))
                    total = int(re.search(r'Всього:\s*<b>(\d+)</b>', text).group(1))
                    match = re.search(r"🏆 <b>Предмет тижня:</b> (.+)", text)
                    if match:
                        best_subject = match.group(1)  # Наприклад: "Геометрія (10.5)"
                        best_subject = best_subject.rsplit(" (", 1)[0]
                    else:
                        best_subject = "Тиша..."
                except Exception:
                    avg, total, best_subject = 0.0, 0, "Тиша..."
            else:
                grades, text = await asyncio.to_thread(
                    get_diary_grades,
                    login,
                    password,
                    days_back,
                    user_id=user_id,
                    db=db,
                    fernet=fernet
                )

                values = [v for v in grades.values() if isinstance(v, (int, float))]
                filtered = {k: v for k, v in grades.items() if isinstance(v, (int, float))}

                avg = round(sum(values) / len(values), 1) if values else 0.0
                if filtered:
                    best_subject, best_val = max(filtered.items(), key=lambda x: x[1])
                else:
                    best_subject = "Тиша..."

                counts = re.findall(r'\((\d+)\s+оцінок\)', text)
                total = sum(map(int, counts))

        # 2. ПЕРЕВІРКА VIP
        # Ексклюзивні стилі — тільки платний VIP (реферальний — базовий стиль)
        is_paid_vip = _is_vip(user_id) and db.get_vip_source(user_id) == "paid"

        await wait_msg.delete()

        # 3. ЛОГІКА ВИБОРУ СТИЛЮ (ЯКЩО ПЛАТНИЙ VIP)
        if is_paid_vip:
            await state.update_data(
                provider=provider,
                username=name,
                avg_grade=avg,
                lessons_count=total,
                top_subject=best_subject,
                is_vip=True
            )
            await message.answer("💎 <b>VIP-доступ активний!</b>\nОберіть стиль для вашого звіту:",
                                 reply_markup=get_styles_kb(),
                                 parse_mode="HTML")
            await state.set_state(WrappedState.waiting_for_style)

        else:
            # 4. ГЕНЕРАЦІЯ ДЛЯ ЗВИЧАЙНИХ ЮЗЕРІВ
            photo_bio = await asyncio.to_thread(
                draw_wrapped,
                provider=provider,
                username=name,
                avg_grade=avg,
                lessons_count=total,
                top_subject=best_subject,
                is_vip=False,
                style_name="default"
            )

            # --- СТВОРЕННЯ КНОПКИ "ПОДІЛИТИСЯ" ---
            share_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📤 Поділитися успіхами", switch_inline_query="wrapped")]
                ]
            )
            # ---------------------------------------

            sent_msg = await message.answer_photo(
                photo=BufferedInputFile(photo_bio.read(), filename="wrapped.png"),
                caption="📸 Твій звіт за тиждень! (Default Style)\n"
                        "<i>Дані взяті з nz.ua або human.ua</i>\n\n"
                        "Хочеш кастомні стилі (Matrix, Gold)? Придбай /vip",
                parse_mode="HTML",
                reply_markup=share_kb  # <--- Додали кнопку сюди
            )

            photo_bio.close()  # Закрываем поток байтов
            del photo_bio  # Удаляем переменную
            gc.collect()  # Просим питон очистить память прямо сейчас

            photo_id = sent_msg.photo[-1].file_id
            WRAPPED_CACHE[user_id] = {
                "file_id": photo_id,
                "caption": f"🔥 Мій середній бал: {avg}! (Стиль: Default)\nХочеш побачити свої результати? Заходь: https://t.me/{BOT_USERNAME}?start={user_id}"
            }
    except Exception:
        try:
            await wait_msg.delete()
        except Exception:
            pass
        logger.exception("Wrapped generation failed for user_id=%s", user_id)
        await message.answer("❌ Щось пішло не так. Спробуйте пізніше.")


@router.callback_query(F.data.startswith("wstyle:"))
async def generate_vip_wrapped(callback: CallbackQuery, state: FSMContext):
    selected_style = callback.data.split(":")[1]

    data = await state.get_data()
    if not data:
        await callback.message.answer("⚠️ Дані застаріли. Спробуйте /wrapped ще раз.")
        return

    await callback.message.edit_text(f"🎨 Генерую стиль: <b>{selected_style.capitalize()}</b>...", parse_mode="HTML")

    try:
        photo_bio = await asyncio.to_thread(
            draw_wrapped,
            provider=data['provider'],
            username=data['username'],
            avg_grade=data['avg_grade'],
            lessons_count=data['lessons_count'],
            top_subject=data['top_subject'],
            is_vip=True,
            style_name=selected_style
        )

        await callback.message.delete()

        # --- КНОПКА ПОДІЛИТИСЯ ДЛЯ VIP ---
        user_id = callback.from_user.id

        share_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Поділитися успіхами", switch_inline_query="wrapped")]
            ]
        )
        # ----------------------------------

        sent_msg = await callback.message.answer_photo(
            photo=BufferedInputFile(photo_bio.read(), filename="wrapped.png"),
            caption=f"📸 Твій звіт у стилі <b>{selected_style.capitalize()}</b>! 🔥\n"
                    f"<i>Дані взяті з nz.ua або human.ua</i>\n\n",
            parse_mode="HTML",
            reply_markup=share_kb  # <--- Додаємо клавіатуру
        )

        photo_bio.close()  # Закрываем поток байтов
        del photo_bio  # Удаляем переменную
        gc.collect()  # Просим питон очистить память прямо сейчас

        photo_id = sent_msg.photo[-1].file_id
        WRAPPED_CACHE[user_id] = {
            "file_id": photo_id,
            "caption": f"🔥 Мій середній бал: {data['avg_grade']}! (Стиль: {selected_style})\nХочеш побачити свої результати? Заходь: https://t.me/{BOT_USERNAME}?start={user_id}"
        }
    except Exception:
        logger.exception("VIP wrapped generation failed for user_id=%s style=%s", callback.from_user.id, selected_style)
        await callback.message.answer("❌ Помилка генерації.")

    await state.clear()


@router.inline_query(F.query == "wrapped")
async def inline_share_handler(query: InlineQuery):
    user_id = query.from_user.id

    # Перевіряємо, чи є у нас в пам'яті фото для цього юзера
    data = WRAPPED_CACHE.get(user_id)

    if not data:
        # Якщо фото немає (бот перезавантажився або юзер не генерував)
        # Можна відправити заглушку або нічого не робити
        return

    # Створюємо результат - Картинка з кешу
    result = InlineQueryResultCachedPhoto(
        id=str(time.time()),  # Унікальний ID результату
        photo_file_id=data["file_id"],
        caption=data["caption"],  # Ось цей текст піде разом з фото!
        parse_mode="HTML"
    )

    # Відповідаємо Telegram'у, щоб він показав цю картинку юзеру
    await query.answer(
        results=[result],
        cache_time=0,
        is_personal=True  # Результат індивідуальний для кожного юзера
    )


@router.message(Command("leaderboard"))
@router.callback_query(F.data == "leaderboard")
async def leaderboard_cmd(event: Union[Message, CallbackQuery]):
    # 1. Зберігаємо ID того, хто викликав команду (caller_id)
    caller_id = event.from_user.id

    if isinstance(event, CallbackQuery):
        message_object = event.message
        await event.answer()
    else:
        message_object = event

    stats = db.get_vip_referral_stats(days=14)

    if not stats:
        await message_object.answer("😔 Поки що немає активних VIP-учасників.")
        return

    top_10 = stats[:10]

    text = "🏆 <b>Топ-10 VIP Амбасадорів (за 2 тижні)</b>\n\n"

    # --- ВИПРАВЛЕННЯ ТУТ ---
    # Використовуємо top_user_id замість user_id, щоб не зламати логіку
    for i, (top_user_id, count) in enumerate(top_10, 1):
        try:
            chat = await message_object.bot.get_chat(top_user_id)
            name = chat.first_name or "Користувач"
            name = escape(name)
        except Exception:
            name = f"ID {str(top_user_id)[:4]}..."

        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} <b>{name}</b> — {count} друзів 🎟\n"

    # 4. Показуємо місце користувача (використовуємо caller_id)
    # Шукаємо caller_id у списку stats
    user_rank = next((idx for idx, (uid, _) in enumerate(stats) if uid == caller_id), None)

    if user_rank is not None:
        my_count = stats[user_rank][1]
        text += f"\n👤 <b>Твоє місце:</b> {user_rank + 1} ({my_count} друзів)"
    else:
        # Перевіряємо, чи є у нього взагалі VIP (опціонально, але текст каже "потрібен VIP")
        text += "\n👤 Ти не береш участі в рейтингу (потрібен VIP статус)."

    await message_object.answer(text, parse_mode="HTML")


@router.message(Command("notify_grades"))
@router.message(F.text.in_(texts.NOTIFY_GRADES_LABELS))
async def turn_notify_grades(message: Message):
    user_id = message.from_user.id
    track_activity(user_id)

    if not _is_vip(user_id):
        await message.answer("🔒 Сповіщення про оцінки доступні лише VIP-користувачам.\n"
                             "🎁 Хочеш VIP безкоштовно? Запроси друга у /vip",
                             reply_markup=vip_upsell_kb())
        return

    _, _, provider = db.get_user(user_id)
    if provider == "human":
        await message.reply(
            "ℹ️ Сповіщення про оцінки наразі доступні лише для щоденника <b>Нові Знання</b>.\n"
            "Для <b>Human</b> ця функція зʼявиться пізніше 👀",
            parse_mode="HTML"
        )
    else:
        enabled = db.toggle_notify_grades(user_id)
        if enabled:
            await message.answer("✅ Сповіщення про нові оцінки увімкнені!")
        else:
            await message.answer("❌ Сповіщення про нові оцінки вимкнені!")


@router.message(Command("notify_digest"))
async def turn_notify_digest(message: Message):
    """Дайджест — opt-in для всіх: VIP отримує щодня, решта по понеділках."""
    user_id = message.from_user.id
    track_activity(user_id)

    if not db.has_credentials(user_id):
        await message.answer("Спочатку увійди у щоденник — /login")
        return

    if db.toggle_notify_digest(user_id):
        if _is_vip(user_id):
            await message.answer(
                "✅ Ранковий дайджест увімкнено!\n"
                "О 7:30 надішлю розклад, ДЗ і посилання на перший онлайн-урок.\n"
                "<i>У дні без уроків нічого не приходить.</i>",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                "✅ Дайджест увімкнено — надсилатиму <b>по понеділках</b> о 7:30.\n"
                "⭐️ Щоденний дайджест — у /vip",
                parse_mode="HTML",
                reply_markup=vip_upsell_kb()
            )
    else:
        await message.answer("❌ Ранковий дайджест вимкнено.")


@router.message(Command("notify_homework"))
async def turn_notify_homework(message: Message):
    user_id = message.from_user.id
    track_activity(user_id)

    if not _is_vip(user_id):
        await message.answer("🔒 Сповіщення про нове ДЗ доступні лише VIP-користувачам.\n"
                             "🎁 Хочеш VIP безкоштовно? Запроси друга у /vip",
                             reply_markup=vip_upsell_kb())
        return

    _, _, provider = db.get_user(user_id)
    if provider == "human":
        await message.reply(
            "ℹ️ Сповіщення про ДЗ наразі працюють лише для щоденника <b>Нові Знання</b>.\n"
            "Для <b>Human</b> ця функція зʼявиться пізніше 👀",
            parse_mode="HTML"
        )
        return

    if db.toggle_notify_homework(user_id):
        await message.answer("✅ Сповіщення про нове ДЗ увімкнені!\n"
                             "<i>Перевіряю щодня, надішлю як тільки вчитель задасть нове.</i>",
                             parse_mode="HTML")
    else:
        await message.answer("❌ Сповіщення про нове ДЗ вимкнені!")


@router.message(Command("notify"))
@router.message(F.text.in_(texts.NOTIFY_LESSONS_LABELS))
async def turn_notify(message: Message):
    user_id = message.from_user.id
    track_activity(user_id)
    if not _is_vip(user_id):
        await message.answer("🔒 Ця функція доступна лише VIP-користувачам.\n"
                             "🎁 Хочеш VIP безкоштовно? Запроси друга у /vip",
                             reply_markup=vip_upsell_kb())
        return
    else:
        if db.user_notify(user_id):
            db.toggle_notify(user_id)
            await message.answer("❌ Нагадування вимкнені!")
        else:
            db.toggle_notify(user_id)
            await message.answer("✅ Нагадування увімкнені! За 5 хв. до урока вам буде надіслано сповіщення.")
