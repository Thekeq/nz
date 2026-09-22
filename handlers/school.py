import asyncio
import time
import re
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from quickchart import QuickChart

import texts
from loader import db, fernet, SEMAPHORE, ADMIN_ID
from utils import user_can_call, track_activity, process_referral_reward, answer_long
from textutils import CAPTION_LIMIT
from keyboards import (
    kb_retry, build_main_kb, build_vip_kb, keyboard_diary, keyboard_hw, add_ai_button,
    result_actions_kb, grades_main_kb, grades_page_kb,
)
from services.diarynz import (
    get_diary_schedule, get_diary_grades, get_diary_news, get_diary_homework,
    get_grade_statement, set_grade_statement_main, InvalidCredentials,
)

router = Router()
logger = logging.getLogger(__name__)
GRADE_LINE_RE = re.compile(
    r"^(.+?):\s*([0-9]+(?:\.[0-9]+)?)\s*\((\d+)\s+оцін", re.IGNORECASE
)


@router.message(Command('diary'))
@router.message(F.text.in_(texts.DIARY_LABELS))
async def get_diary(message: Message, state: FSMContext):
    user_id = message.from_user.id
    track_activity(user_id)
    allowed = await user_can_call(user_id, "diary", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    if db.has_credentials(user_id):
        try:
            login, enc_password = db.get_user(user_id)
            password = fernet.decrypt(enc_password.encode()).decode()
            is_tiktok = False
            if message.from_user.id == ADMIN_ID:
                from handlers.admin import TIKTOK_MODE
                is_tiktok = TIKTOK_MODE

            async with SEMAPHORE:
                schedule = await asyncio.to_thread(
                    get_diary_schedule,
                    login,
                    password,
                    is_tiktok_mode=is_tiktok,
                    user_id=user_id,
                    db=db,
                    fernet=fernet
                )
                if schedule:  # или другой признак “ок”
                    db.set_creds_verified(user_id, 1)
                    # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
                    await process_referral_reward(user_id)

            if message.chat.type == "private":
                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if is_vip:
                    keyboard = await keyboard_diary()
                    await message.reply("✅ Ось ваш розклад:", reply_markup=build_vip_kb())
                    await answer_long(message, schedule, reply_markup=result_actions_kb(user_id, keyboard), disable_web_page_preview=True)
                else:
                    keyboard = await keyboard_diary()
                    await message.reply("✅ Ось ваш розклад:", reply_markup=build_main_kb())
                    await answer_long(message, schedule, reply_markup=result_actions_kb(user_id, keyboard), disable_web_page_preview=True)
            else:
                await message.answer(f"{schedule}")

        except InvalidCredentials as e:
            await message.answer(f"❌ {e}", reply_markup=kb_retry)

        except Exception:
            logger.exception("Failed to get diary for user_id=%s", user_id)
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
@router.message(F.text.in_(texts.HOMEWORK_LABELS))
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

    login, enc_password = db.get_user(user_id)
    password = fernet.decrypt(enc_password.encode()).decode()

    # Отримуємо базову клавіатуру (дні тижня)
    base_keyboard = await keyboard_hw()

    try:
        async with SEMAPHORE:
            text = await asyncio.to_thread(
                get_diary_homework,
                login,
                password,
                user_id=user_id,
                db=db,
                fernet=fernet
            )

            if text:
                db.set_creds_verified(user_id, 1)
                await process_referral_reward(user_id)

        # --- ЗМІНИ ТУТ ---
        # Модифікуємо клавіатуру, додаючи кнопку ШІ з актуальним текстом
        final_keyboard = add_ai_button(base_keyboard, text)

        await answer_long(
            message, text,
            reply_markup=result_actions_kb(user_id, final_keyboard),
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception:
        logger.exception("Failed to get homework for user_id=%s", user_id)
        await message.answer("❌ Не вдалося отримати ДЗ. Спробуйте ще раз пізніше.", reply_markup=kb_retry)


@router.message(Command('news'))
@router.message(F.text.in_(texts.NEWS_LABELS))
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
        login, enc_password = db.get_user(user_id)
        if not login or not enc_password:
            await message.answer("❌ У базі немає ваших облікових даних. Виконайте /start заново.")
            return
        password = fernet.decrypt(enc_password.encode()).decode()

        async with SEMAPHORE:
            text = await asyncio.to_thread(
                get_diary_news,
                login,
                password,
                10,
                user_id=user_id,
                db=db,
                fernet=fernet
            )
            if text:
                db.set_creds_verified(user_id, 1)
                # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
                await process_referral_reward(user_id)

        await answer_long(message, text, reply_markup=result_actions_kb(user_id))
    except Exception:
        logger.exception("Failed to get news for user_id=%s", user_id)
        await message.answer("❌ Не вдалося отримати новини. Спробуйте пізніше.", reply_markup=kb_retry)


def _no_grades_note(text: str) -> str:
    """Пояснення замість порожньої діаграми: канікули, новий семестр
    або вчителі ще нічого не виставили."""
    if parse_grades_text(text):
        return ""
    return "📭 <b>Оцінок поки немає</b> — нема чого рахувати й малювати.\n\n"


def photo_grades(text):
    items = parse_grades_text(text)  # Твоя функция: [(Subject, Avg, Count), ...]
    if not items:
        return None  # нема з чого малювати павутинку — вийде порожня картинка

    # Фильтруем топ-5 предметов для красоты (или берем все, если влезет)
    # Берем только название и средний балл
    labels = [item[0][:22] for item in items]  # Обрезаем длинные названия
    data = [item[1] for item in items]

    # 3. Рисуем Radar Chart (Паутинка)
    qc = QuickChart()
    qc.width = 800
    qc.height = 800  # Квадрат
    qc.device_pixel_ratio = 2.0
    qc.background_color = "#1e1e1e"

    qc.config = {
        "type": "radar",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": "Успішність",
                "data": data,
                "backgroundColor": "rgba(52, 152, 219, 0.4)",  # Полупрозрачный синий
                "borderColor": "#3498db",
                "pointBackgroundColor": "#fff",
                "borderWidth": 3
            }]
        },
        "options": {
            "legend": {"display": False},
            "scale": {
                "ticks": {
                    "beginAtZero": True,
                    "max": 12,  # Максимум 12 баллов
                    "backdropColor": "transparent",  # Прозрачный фон цифр
                    "fontColor": "#bdc3c7"
                },
                "gridLines": {"color": "#444"},  # Сетка
                "pointLabels": {
                    "fontSize": 16,
                    "fontColor": "#fff",
                    "fontStyle": "bold"
                }
            },
            "title": {
                "display": True,
                "text": "Твоя статистика (VIP)",
                "fontColor": "#fff",
                "fontSize": 24
            }
        }
    }
    return qc.get_url()


@router.message(Command('avg_grades'))
@router.message(F.text.in_(texts.GRADES_LABELS))
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
        login, enc_password = db.get_user(user_id)
        password = fernet.decrypt(enc_password.encode()).decode()

        async with SEMAPHORE:
            grades, text = await asyncio.to_thread(
                get_diary_grades,
                login,
                password,
                user_id=user_id,
                db=db,
                fernet=fernet
            )

        # перевіряємо VIP
        vip_flag, expires = db.get_vip_status(user_id)
        now_ts = int(time.time())
        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

        if is_vip:
            extra = build_vip_grade_summary(text)
            final_text = f"{text}\n\n{extra}" if extra else text
            final_text = _no_grades_note(text) + final_text
            set_grade_statement_main(user_id, final_text)
            _, grade_pages = get_grade_statement(user_id)
            actions_kb = result_actions_kb(user_id, grades_main_kb(len(grade_pages)))

            url = photo_grades(text)

            if url:
                # ліміт підпису до фото — 1024, а не 4096: у кого багато
                # предметів, підпис не влазить, тому текст іде окремо
                if len(final_text) <= CAPTION_LIMIT:
                    await message.answer_photo(
                        photo=url, caption=final_text, parse_mode="HTML",
                        reply_markup=actions_kb
                    )
                else:
                    await message.answer_photo(photo=url, caption="📊 Твоя статистика")
                    await answer_long(
                        message, final_text,
                        reply_markup=actions_kb, parse_mode="HTML"
                    )
            else:
                await answer_long(message, final_text, reply_markup=actions_kb, parse_mode="HTML")

        else:
            final_text = _no_grades_note(text) + text
            set_grade_statement_main(user_id, final_text)
            _, grade_pages = get_grade_statement(user_id)
            await answer_long(
                message,
                final_text,
                reply_markup=result_actions_kb(user_id, grades_main_kb(len(grade_pages))),
                parse_mode="HTML",
            )

    except InvalidCredentials as e:
        await message.answer(f"❌ {e}", reply_markup=kb_retry)

    except Exception:
        logger.exception("Failed to get grades for user_id=%s", user_id)
        await message.answer(
            "❌ Сталася помилка. Спробуйте пізніше.",
            reply_markup=kb_retry
        )


@router.callback_query(F.data.startswith("grades_page:"))
async def grades_page_selected(callback: CallbackQuery):
    payload = callback.data.split(":", 1)[1]
    if payload == "noop":
        await callback.answer()
        return

    try:
        page = int(payload)
    except ValueError:
        await callback.answer("⚠️ Сторінку не знайдено.", show_alert=True)
        return

    _, pages = get_grade_statement(callback.from_user.id)
    if not pages or page < 0 or page >= len(pages):
        await callback.answer("⚠️ Виписка застаріла. Відкрий оцінки ще раз.", show_alert=True)
        return

    await callback.answer()
    markup = result_actions_kb(
        callback.from_user.id,
        grades_page_kb(page, len(pages)),
    )
    if callback.message and callback.message.text is not None:
        await callback.message.edit_text(
            pages[page], parse_mode="HTML", reply_markup=markup
        )
    elif callback.message:
        # VIP може показувати головну сторінку як фото: текстову виписку
        # отправляем отдельным сообщением, чтобы не терять график.
        await callback.message.answer(
            pages[page], parse_mode="HTML", reply_markup=markup
        )


@router.callback_query(F.data == "grades_main")
async def grades_main_selected(callback: CallbackQuery):
    main_text, pages = get_grade_statement(callback.from_user.id)
    if not main_text or not pages:
        await callback.answer("⚠️ Виписка застаріла. Відкрий оцінки ще раз.", show_alert=True)
        return

    await callback.answer()
    markup = result_actions_kb(
        callback.from_user.id,
        grades_main_kb(len(pages)),
    )
    if callback.message and callback.message.text is not None:
        await callback.message.edit_text(
            main_text, parse_mode="HTML", reply_markup=markup
        )
    elif callback.message:
        await callback.message.answer(
            main_text, parse_mode="HTML", reply_markup=markup
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

    login, enc_password = db.get_user(user_id)
    if not login:
        await callback.message.answer("❌ Не знайдено ваш акаунт з логіном у базі.")
        return
    password = fernet.decrypt(enc_password.encode()).decode()

    try:
        async with SEMAPHORE:
            schedule = await asyncio.to_thread(
                get_diary_schedule,
                login,
                password,
                days=[day],
                user_id=user_id,
                db=db,
                fernet=fernet
            )
            if schedule:
                db.set_creds_verified(user_id, 1)
                # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
                await process_referral_reward(user_id)

    except Exception:
        logger.exception("Failed to get diary day for user_id=%s day=%s", user_id, day)
        await callback.message.answer("❌ Не вдалося отримати розклад. Спробуйте пізніше.")
        return

    keyboard = await keyboard_diary(day)
    if not schedule:
        await callback.message.answer(f"📅 Розклад на {day} не знайдено або сталася помилка.")
    else:
        try:
            await callback.message.edit_text(f"{schedule}", reply_markup=result_actions_kb(user_id, keyboard), parse_mode="HTML",
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

    login, enc_password = db.get_user(user_id)
    if not login:
        await callback.message.answer("❌ Не знайдено ваш акаунт.")
        return
    password = fernet.decrypt(enc_password.encode()).decode()

    try:
        async with SEMAPHORE:
            schedule = await asyncio.to_thread(
                get_diary_homework,
                login,
                password,
                days=[day],
                user_id=user_id,
                db=db,
                fernet=fernet
            )

    except Exception:
        logger.exception("Failed to get homework day for user_id=%s day=%s", user_id, day)
        await callback.message.answer("❌ Не вдалося отримати ДЗ. Спробуйте пізніше.")
        return

    # Отримуємо чисту клавіатуру навігації
    base_keyboard = await keyboard_hw(day)

    # --- ЗМІНИ ТУТ ---
    # Прикріплюємо кнопку ШІ до НОВОГО тексту (schedule)
    final_keyboard = add_ai_button(base_keyboard, schedule)

    try:
        await callback.message.edit_text(
            f"{schedule}",
            parse_mode="HTML",
            reply_markup=result_actions_kb(user_id, final_keyboard),
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
