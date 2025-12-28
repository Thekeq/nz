import asyncio
import time
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command

from loader import db, fernet, SEMAPHORE
from utils import user_can_call, track_activity, process_referral_reward
from keyboards import kb_retry, build_main_kb, build_vip_kb, keyboard_diary, keyboard_hw, add_ai_button
from services.diarynz import get_diary_schedule, get_diary_grades, get_diary_news, get_diary_homework, \
    InvalidCredentials
from services.diaryhuman import get_diary_schedule_human, get_diary_homework_human, get_diary_news_human, \
    get_diary_grades_human

router = Router()
GRADE_LINE_RE = re.compile(r"^(.+?):\s*(\d+|.*?)\s*//\s*(.*)$")


# ... (Сюди встав функції get_diary, homework_cmd, news_command, get_grades з main.py)
# Важливо: заміни декоратори @router.message(...) і перевір імпорти.
# Я покажу приклад для однієї функції, інші аналогічно копіюються.

@router.message(Command('diary'))
@router.message(F.text == "📅 Розклад")
async def get_diary(message: Message, state: FSMContext):
    user_id = message.from_user.id
    track_activity(user_id)
    allowed = await user_can_call(user_id, "diary", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    if db.has_credentials(user_id):
        try:
            login, enc_password, provider = db.get_user(user_id)
            password = fernet.decrypt(enc_password.encode()).decode()

            async with SEMAPHORE:
                if provider == "human":
                    schedule = await asyncio.to_thread(get_diary_schedule_human, login, password)
                else:
                    schedule = await asyncio.to_thread(get_diary_schedule, login, password)
                if schedule:  # или другой признак “ок”
                    db.set_creds_verified(user_id, 1)
                    # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
                    await process_referral_reward(user_id)

            if message.chat.type == "private":
                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if is_vip:
                    keyboard = await keyboard_diary(provider)
                    await message.reply("✅ Вот ваш розклад:", reply_markup=build_vip_kb())
                    await message.answer(f"{schedule}", disable_web_page_preview=True, reply_markup=keyboard)
                else:
                    keyboard = await keyboard_diary(provider)
                    await message.reply("✅ Вот ваш розклад:", reply_markup=build_main_kb())
                    await message.answer(f"{schedule}", disable_web_page_preview=True, reply_markup=keyboard)
            else:
                await message.answer(f"{schedule}")

        except InvalidCredentials as e:
            await message.answer(f"❌ {e}", reply_markup=kb_retry)

        except Exception as e:
            print(e)
            await message.answer(
                "❌ Сталася помилка. Спробуйте пізніше.",
                reply_markup=kb_retry
            )
    else:
        if message.chat.type == "private":
            await message.answer("Щоб почати, увійдіть у свій аккаунт\n"
                                 "/login")
        else:
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт")


@router.message(Command("homework"))
@router.message(F.text == "📕 Д/з")
async def homework_cmd(message: Message, state: FSMContext):
    user_id = message.from_user.id
    track_activity(user_id)

    allowed = await user_can_call(user_id, "homework", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд — не перевантажуй сайт.")
        return

    if not db.has_credentials(user_id):
        await message.answer("Щоб почати, увійдіть у свій аккаунт\n/login")
        return

    login, enc_password, provider = db.get_user(user_id)
    password = fernet.decrypt(enc_password.encode()).decode()

    # Отримуємо базову клавіатуру (дні тижня)
    base_keyboard = await keyboard_hw(provider)

    try:
        async with SEMAPHORE:
            if provider == "human":
                text = await asyncio.to_thread(get_diary_homework_human, login, password)
            else:
                text = await asyncio.to_thread(get_diary_homework, login, password)

            if text:
                db.set_creds_verified(user_id, 1)
                await process_referral_reward(user_id)

        # --- ЗМІНИ ТУТ ---
        # Модифікуємо клавіатуру, додаючи кнопку ШІ з актуальним текстом
        final_keyboard = add_ai_button(base_keyboard, text)

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=final_keyboard,  # Відправляємо вже з кнопкою ШІ
            disable_web_page_preview=True
        )

    except Exception as e:
        await message.answer(f"❌ Помилка при отриманні ДЗ: {e}\nСпробуйте ще раз пізніше.")


@router.message(Command('news'))
@router.message(F.text == "📰 Новини")
async def news_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    track_activity(user_id)
    # антиспам
    allowed = await user_can_call(user_id, "news", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    # потрібен логін NZ
    if not db.has_credentials(user_id):
        if message.chat.type == "private":
            await message.answer("Щоб почати, увійдіть у свій аккаунт\n"
                                 "/login")
        else:
            await message.reply("Щоб почати, напишіть боту в особисті повідомлення /start та увійдіть у свій акаунт")
        return

    try:
        login, enc_password, provider = db.get_user(user_id)
        if not login or not enc_password:
            await message.answer("❌ У базі немає ваших облікових даних. Виконайте /start заново.")
            return
        password = fernet.decrypt(enc_password.encode()).decode()

        async with SEMAPHORE:
            if provider == "human":
                text = await asyncio.to_thread(get_diary_news_human, login, password, 10)
            else:
                text = await asyncio.to_thread(get_diary_news, login, password, 10)
            if text:
                db.set_creds_verified(user_id, 1)
                # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
                await process_referral_reward(user_id)

        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Помилка при отриманні новин: {e}")


@router.message(Command('avg_grades'))
@router.message(F.text == "📊 Статистика")
async def get_grades(message: Message, state: FSMContext):
    user_id = message.from_user.id
    track_activity(user_id)
    allowed = await user_can_call(user_id, "avg_grades", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    if not db.has_credentials(user_id):
        if message.chat.type == "private":
            await message.answer("Щоб почати, увійдіть у свій аккаунт\n/login")
        else:
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт"
            )
        return

    try:
        login, enc_password, provider = db.get_user(user_id)
        password = fernet.decrypt(enc_password.encode()).decode()

        async with SEMAPHORE:
            if provider == "human":
                text = await asyncio.to_thread(get_diary_grades_human, login, password)
            else:
                grades, text = await asyncio.to_thread(get_diary_grades, login, password)

        # перевіряємо VIP
        vip_flag, expires = db.get_vip_status(user_id)
        now_ts = int(time.time())
        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

        if is_vip:
            if provider == "human":
                extra = build_vip_grade_summary_human(text)
            else:
                extra = build_vip_grade_summary(text)
            if extra:
                await message.answer(f"{text}\n\n{extra}")
            else:
                await message.answer(text)
        else:
            await message.answer(text)

    except InvalidCredentials as e:
        await message.answer(f"❌ {e}", reply_markup=kb_retry)

    except Exception as e:
        print(e)
        await message.answer(
            "❌ Сталася помилка. Спробуйте пізніше.",
            reply_markup=kb_retry
        )


@router.callback_query(lambda c: c.data and c.data.startswith("diary_day:"))
async def diary_day_selected(callback: CallbackQuery):
    user_id = callback.from_user.id
    allowed = await user_can_call(user_id, "diary", cooldown=1)
    if not allowed:
        await callback.answer("⏳ Повільніше 🙂", show_alert=True)
        return
    await callback.answer()
    if not db.has_credentials(user_id):
        await callback.message.answer("❌ Ви ще не авторизовані. Використайте /start для входу.")
        return

    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    if not (bool(vip_flag) and (expires == 0 or expires > now_ts)):
        await callback.message.answer("🔒 Розклад на інші дні доступний у /vip ⭐️")
        return

    day = callback.data.split(":", 1)[1]

    login, enc_password, provider = db.get_user(user_id)
    if not login:
        await callback.message.answer("❌ Не знайдено ваш акаунт з логіном у базі.")
        return
    password = fernet.decrypt(enc_password.encode()).decode()

    try:
        async with SEMAPHORE:
            if provider == "human":
                schedule = await asyncio.to_thread(get_diary_schedule_human, login, password, days=[day])
            else:
                schedule = await asyncio.to_thread(get_diary_schedule, login, password, days=[day])
            if schedule:
                db.set_creds_verified(user_id, 1)
                # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
                await process_referral_reward(user_id)

    except Exception as e:
        await callback.message.answer(f"❌ Помилка при отриманні розкладу: {e}")
        return

    keyboard = await keyboard_diary(provider)
    if not schedule:
        await callback.message.answer(f"📅 Розклад на {day} не знайдено або сталася помилка.")
    else:
        try:
            await callback.message.edit_text(f"{schedule}", reply_markup=keyboard, parse_mode="HTML",
                                             disable_web_page_preview=True)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass  # всё ок, просто тот же текст
            else:
                raise


@router.callback_query(lambda c: c.data and c.data.startswith("diary_hw:"))
async def diary_hw_selected(callback: CallbackQuery):
    user_id = callback.from_user.id
    allowed = await user_can_call(user_id, "diary", cooldown=1)
    if not allowed:
        await callback.answer("⏳ Повільніше 🙂", show_alert=True)
        return
    # Не забуваємо відповідати на callback, щоб кружечок зник
    await callback.answer()

    if not db.has_credentials(user_id):
        await callback.message.answer("❌ Ви ще не авторизовані.")
        return

    day = callback.data.split(":", 1)[1]

    login, enc_password, provider = db.get_user(user_id)
    if not login:
        await callback.message.answer("❌ Не знайдено ваш акаунт.")
        return
    password = fernet.decrypt(enc_password.encode()).decode()

    try:
        async with SEMAPHORE:
            if provider == "human":
                schedule = await asyncio.to_thread(get_diary_homework_human, login, password, mode=day)
            else:
                schedule = await asyncio.to_thread(get_diary_homework, login, password, days=[day])

    except Exception as e:
        await callback.message.answer(f"❌ Помилка: {e}")
        return

    # Отримуємо чисту клавіатуру навігації
    base_keyboard = await keyboard_hw(provider)

    # --- ЗМІНИ ТУТ ---
    # Прикріплюємо кнопку ШІ до НОВОГО тексту (schedule)
    final_keyboard = add_ai_button(base_keyboard, schedule)

    try:
        await callback.message.edit_text(
            f"{schedule}",
            parse_mode="HTML",
            reply_markup=final_keyboard,  # Оновлюємо клавіатуру разом з текстом
            disable_web_page_preview=True
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise


def parse_grades_text(text: str):
    """Парсимо рядки типу 'Алгебра: 10.5 (2 оцінок)' → [(subject, avg, count), ...]"""
    items = []
    for line in text.splitlines():
        line = line.strip()
        m = GRADE_LINE_RE.match(line)
        if not m:
            continue
        subject = m.group(1).strip()
        avg = float(m.group(2))
        count = int(m.group(3))
        items.append((subject, avg, count))
    return items


def build_vip_grade_summary(text: str) -> str:
    """Повертає 2 блоки:
    📈 Розумний прогноз оцінок
    📊 Рейтинг предметів (найкращий / найскладніший)
    """
    items = parse_grades_text(text)
    if not items:
        return ""

    # зважений загальний середній бал
    total_sum = 0.0
    total_count = 0
    for _, avg, cnt in items:
        total_sum += avg * cnt
        total_count += cnt
    overall = round(total_sum / total_count, 2) if total_count else 0.0

    # найкращий / найгірший предмет за середнім балом
    items_sorted = sorted(items, key=lambda x: x[1], reverse=True)
    best_subj, best_avg, _ = items_sorted[0]
    worst_subj, worst_avg, _ = items_sorted[-1]

    forecast_block = (
        "📈 <b>Розумний прогноз оцінок</b>\n"
        f"Якщо ти збережеш поточний темп, прогнозований загальний середній бал буде "
        f"<b>{overall}</b> до кінця семестру.\n\n"
    )

    rating_block = (
        "📊 <b>Рейтинг предметів</b>\n"
        f"Найкращий предмет: <b>{best_subj}</b> — середній <b>{best_avg}</b>\n"
        f"Найскладніший предмет: <b>{worst_subj}</b> — середній <b>{worst_avg}</b>"
    )

    return forecast_block + rating_block


def build_vip_grade_summary_human(text: str) -> str:
    """
    VIP-блоки для Human.
    Робить:
      📈 Розумний прогноз — по тренду динаміки
      📊 "Рейтинг" — чесно: поки без предметів, але показує тенденцію та найкращий/найгірший день
    """
    m = HUMAN_AVG_RE.search(text)
    overall = float(m.group(1)) if m else None

    dyn = [(mm, float(avg)) for mm, avg in HUMAN_DYN_RE.findall(text)]
    # dyn: [("21.11", 9.33), ...] у порядку як у тексті (у тебе найсвіжіші зверху)
    # переведемо так, щоб старе -> нове
    dyn_rev = list(reversed(dyn))

    if overall is None and not dyn_rev:
        return ""

    # ---- прогноз ----
    forecast_line = "Немає достатньо даних для прогнозу 🙃"
    trend_line = ""

    if len(dyn_rev) >= 2:
        first_d, first_v = dyn_rev[0]
        last_d, last_v = dyn_rev[-1]
        delta = last_v - first_v

        # дуже простий прогноз: поточний середній + половина тренду за період
        base = overall if overall is not None else last_v
        predicted = base + (delta * 0.5)

        # обрізаємо в межі 1..12 (типова шкала)
        predicted = max(1.0, min(12.0, predicted))

        arrow = "📈" if delta > 0.05 else ("📉" if delta < -0.05 else "➖")
        trend_line = f"{arrow} Тенденція за період: <b>{delta:+.2f}</b>"

        forecast_line = (
            f"Якщо ти збережеш поточну тенденцію, очікуваний середній буде близько "
            f"<b>{predicted:.2f}</b> найближчим часом."
        )

    elif overall is not None:
        forecast_line = (
            f"Якщо тримати поточний темп, середній залишиться близько <b>{overall:.2f}</b>."
        )

    # ---- “рейтинг” (не предметів, а днів) ----
    rating_block = ""
    if dyn_rev:
        best_day, best_val = max(dyn_rev, key=lambda x: x[1])
        worst_day, worst_val = min(dyn_rev, key=lambda x: x[1])
        rating_block = (
            "📊 <b>Аналіз динаміки</b>\n"
            f"Найкращий день: <b>{best_day}</b> — <b>{best_val:.2f}</b>\n"
            f"Найскладніший день: <b>{worst_day}</b> — <b>{worst_val:.2f}</b>"
        )
    else:
        rating_block = (
            "📊 <b>Аналіз динаміки</b>\n"
            "—"
        )

    forecast_block = (
        "📈 <b>Розумний прогноз оцінок</b>\n"
        f"{forecast_line}\n"
    )
    if trend_line:
        forecast_block += f"{trend_line}\n"

    return forecast_block + "\n" + rating_block

# ... (Встав сюди homework_cmd, news_command, get_grades, diary_day_selected, diary_hw_selected з main.py)
# ... Також helper-функції build_vip_grade_summary, parse_grades_text
