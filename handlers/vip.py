import asyncio
import time
import datetime
import random
import re
import gc
from html import escape

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, BufferedInputFile, \
    InlineQueryResultCachedPhoto, InlineQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from loader import db, HW_AI_CACHE, WRAPPED_CACHE, fernet, SEMAPHORE, ADMIN_ID
from utils import track_activity, fix_ai_response, user_can_call
from keyboards import build_vip_kb, share_kb, payment_keyboard, get_styles_kb
from states import AIStates, WrappedState
from services.ai import ai
from services.drawer import draw_wrapped
from services.diaryhuman import get_diary_grades_human
from services.diarynz import get_diary_grades

router = Router()


@router.message(Command('vip'))
@router.message(F.text == "⭐️ Free VIP")
async def vip_func(message: Message):
    user_id = message.from_user.id
    track_activity(user_id)
    # Дозволяємо відкривати VIP-меню навіть без кредів (оплата не залежить від NZ)
    db.ensure_user(user_id)

    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

    if is_vip:
        date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y")
        if expires == 0:
            date_str = "НАЗАВЖДИ :)"
        await message.answer(
            f"⭐️ Ви маєте VIP до <b>{date_str}</b>\n\n"
            f"✨ /ai — <b>Використання ШІ</b>\n"
            f"👕 /wrapped — <b>Ексклюзивні теми (Matrix, Gold, Ocean)</b>\n"
            f"📅 /diary_days (/diary) — <b>Перегляд розкладу по дням</b>\n"
            f"📊 /avg_grades — <b>Розумний прогноз оцінок, рейтинг предметів</b>\n"
            f"⏰ /notify — <b>Нагадування перед уроками</b>\n"
            f"🔔 /notify_grades — <b>Сповіщення о нових оцінках (nz)</b>\n"
            f"⚡ <b>Пріоритетні запити та підтримка</b>\n\n"
            f"🎁 <b>БЕЗКОШТОВНИЙ VIP</b> на 5 днів за кожних 3 друзів\n"
            f"Ваше реферальне посилання:\n"
            f"https://t.me/nzdiary_bot?start={user_id}",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "🎁 Безкоштовний VIP: 3 друга → 5 днів VIP\n"
            f"Ваше реферальне посилання: https://t.me/nzdiary_bot?start={user_id}\n\n"
            f"⭐️ <b>Перелік VIP-Функцій:</b>\n"
            f"🎨 Ексклюзивні теми (Matrix, Gold, Ocean)\n"
            f"✨ Використання ШІ-асистента\n"
            f"📅 Перегляд розкладу по дням\n"
            f"⏰ Нагадування за 5-хв до уроку\n"
            f"🔔 Сповіщення про нові оцінки (NZ)\n"
            f"📊 Розширений рейтинг та статистика\n"
            f"⚡ Пріоритетні запити та швидка підтримка\n\n"
            f"💎 <b>Оберіть спосіб оплати VIP:</b>\n"
            f"⭐️ <b>Telegram Stars (75 ⭐️)</b>\n"
            f"Натисніть кнопку нижче. Активація миттєва.\n\n"
            f"🐈 <b>Monobank (Банка) 75 грн</b>\n"
            f"Реквізити банки: <code>4874 1000 2294 2034</code>\n"
            f"🔗 <a href='https://send.monobank.ua/jar/3bXsmYAcTp'>Натисніть тут, щоб відкрити Банку</a>\n\n"
            f"⚠️ <b>ВАЖЛИВО!</b> У коментар до платежу вставте свій ID:\n"
            f"👉 <code>{user_id}</code> 👈\n"
            f"<i>(натисніть щоб скопіювати)</i>", reply_markup=share_kb(user_id)
        )

        prices = [LabeledPrice(label="VIP доступ на 1 місяць", amount=75)]  # сума в XTR-центах

        await message.answer_invoice(
            title="VIP Доступ",
            description="Миттєвий доступ до всіх перелічених функцій на 1 місяць 🚀",
            payload=f"vip_{user_id}_{int(datetime.datetime.now().timestamp())}",
            provider_token="",  # токен від Telegram Payments (Stars)
            currency="XTR",
            prices=prices,
            start_parameter="vip_payment",
            reply_markup=payment_keyboard()
        )


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    user_id = message.from_user.id
    db.set_vip(user_id, days=30)
    db.set_tokens(user_id, 1000000)
    vip, expires = db.get_vip_status(user_id)
    date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y %H:%M")
    await message.answer(f"✅ Ви отримали VIP доступ до {date_str}\n"
                         f"💎 Вам нараховано <b>1 000 000 AI-токенів</b>!", reply_markup=build_vip_kb())


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await pre_checkout_q.answer(ok=True)


@router.message(Command("ai"))
@router.message(F.text == "✨ ШІ")
async def ai_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
    if is_vip:
        current_tokens = db.get_tokens(user_id)
        await state.set_state(AIStates.waiting_input)
        await message.reply(
            "✨ Напиши питання текстом або надішли фото з підписом\n"
            f"У вас ще 💎 <b>{current_tokens:,}</b> токенів", reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Вийти з режиму ШІ ✨", callback_data="exit_ai")]
                ]
            )
        )
    else:
        await message.reply("🔒 Ця функція доступна лише /vip користувачам")


@router.message(AIStates.waiting_input)
async def ai_input_text_or_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id

    current_tokens = db.get_tokens(user_id)
    if current_tokens <= 0:
        await message.reply("❌ У вас закінчилися AI-токени! Поновіть VIP, щоб отримати ще 1 млн.")
        await state.clear()
        return

    if message.text:
        if len(message.text) > 400:
            await message.reply(f"Максимальна довжина тексту 400 символів. Ви ввели {len(message.text)}")
            return
        user_prompt = message.text.strip()
        try:
            # Викликаємо оновлену функцію, яка повертає ДВА значення
            answer, cost = await ai(user_prompt)
            # Списуємо токени
            db.deduct_tokens(user_id, cost)
            answer = fix_ai_response(answer)
            answer += f"\n\n🔄 <i>Щоб задати нове питання — натисни</i> /ai"
            # Можна показувати залишок (опціонально)
            answer += f"\n💎 Витрачено: {cost} токенів."

            await message.reply(answer, parse_mode="HTML", disable_web_page_preview=True)

        except Exception as e:
            print(f"AI Error: {e}")
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

        try:
            # Викликаємо оновлену функцію з фото
            answer, cost = await ai(caption, img_bytes)
            # Списуємо токени
            db.deduct_tokens(user_id, cost)

            answer = fix_ai_response(answer)
            answer += "\n\n🔄 <i>Щоб задати нове питання — натисни</i> /ai"
            answer += f"\n💎 Витрачено: {cost} токенів."

            await message.reply(answer, parse_mode="HTML", disable_web_page_preview=True)

        except Exception as e:
            print(f"AI Error: {e}")
            await message.reply("😫 Халепа... спробуйте трохи змінити запитання!")

        await state.clear()
        return

    # если ни текста, ни фото
    await message.reply("Надішли текст або фото з підписом 🙂")


@router.callback_query(F.data.startswith("ai_hw:"))
async def handle_ai_homework(callback: CallbackQuery):
    user_id = callback.from_user.id
    temp_id = callback.data.split(":")[1]
    hw_text = HW_AI_CACHE.get(temp_id)

    if not hw_text:
        await callback.answer("⚠️ Дані застаріли, спробуйте ще раз. /homework")
        return

    current_tokens = db.get_tokens(user_id)
    if current_tokens <= 0:
        await callback.answer("❌ Закінчилися AI-токени!", show_alert=True)
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
        # 3. Списуємо
        db.deduct_tokens(user_id, cost)
        response_text += f"\n\n💎 Витрачено: {cost} токенів."

        await callback.message.answer(response_text, parse_mode="HTML")

    except Exception as e:
        print(f"AI Error: {e}")
        await callback.message.answer("❌ Не вдалося отримати пораду. Спробуй пізніше.")
    # Видаляємо з кешу, щоб не забивати пам'ять
    del HW_AI_CACHE[temp_id]


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
            if provider == "human":
                text = await asyncio.to_thread(get_diary_grades_human, login, password, 7)
                try:
                    avg = float(re.search(r'Середній:</b>\s*([\d.]+)', text).group(1))
                    total = int(re.search(r'Всього:\s*<b>(\d+)</b>', text).group(1))
                    match = re.search(r"🏆 <b>Предмет тижня:</b> (.+)", text)
                    if match:
                        best_subject = match.group(1)  # Наприклад: "Геометрія (10.5)"
                        best_subject = best_subject.rsplit(" (", 1)[0]
                    else:
                        best_subject = "Не знайдено"
                except:
                    avg, total = 0.0, 0
            else:
                grades, text = await asyncio.to_thread(get_diary_grades, login, password, 7)

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
        vip_flag, expires = db.get_vip_status(user_id)
        now_ts = int(time.time())
        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

        await wait_msg.delete()

        # 3. ЛОГІКА ВИБОРУ СТИЛЮ (ЯКЩО VIP)
        if is_vip:
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
                        "<i><a href='https://nz.ua/schedule/grades-statement'>Дані взяті з nz.ua</a></i>\n\n"
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
                "caption": f"🔥 Мій середній бал: {avg}! (Стиль: Default)\nХочеш побачити свої результати? Заходь: https://t.me/nzdiary_bot?start={user_id}"
            }
    except Exception as e:
        try:
            await wait_msg.delete()
        except:
            pass
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
                    f"<i><a href='https://nz.ua/schedule/grades-statement'>Дані взяті з nz.ua</a></i>",
            parse_mode="HTML",
            reply_markup=share_kb  # <--- Додаємо клавіатуру
        )

        photo_bio.close()  # Закрываем поток байтов
        del photo_bio  # Удаляем переменную
        gc.collect()  # Просим питон очистить память прямо сейчас

        photo_id = sent_msg.photo[-1].file_id
        WRAPPED_CACHE[user_id] = {
            "file_id": photo_id,
            "caption": f"🔥 Мій середній бал: {data['avg_grade']}! (Стиль: {selected_style})\nХочеш побачити свої результати? Заходь: https://t.me/nzdiary_bot?start={user_id}"
        }
    except Exception as e:
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
async def leaderboard_cmd(message: Message):
    # 1. Отримуємо статистику (всі VIP + їхні запрошення за 2 тижні)
    stats = db.get_vip_referral_stats(days=14)

    if not stats:
        await message.answer("😔 Поки що немає активних VIP-учасників.")
        return

    # 2. Беремо топ-10
    top_10 = stats[:10]

    text = "🏆 <b>Топ-10 VIP Амбасадорів (за 2 тижні)</b>\n\n"
    text += "<i><b>🎁 Кожні 2 тижні серед усіх VIP користувачів проводиться розіграш подарунка за 100 ⭐️</b></i>\n"
    text += "<i>Запрошуй друзів, щоб піднятися в топі та отримати більше шансів у розіграші!</i>\n\n"

    # 3. Формуємо список
    for i, (user_id, count) in enumerate(top_10, 1):
        try:
            # Пробуємо отримати ім'я користувача від Telegram
            chat = await message.bot.get_chat(user_id)
            name = chat.first_name or "Користувач"
            # Екрануємо ім'я, щоб не зламало HTML (наприклад, якщо ім'я "<Ivan>")
            name = escape(name)
        except Exception:
            name = f"ID {str(user_id)[:4]}..."

        # Медальки для перших трьох місць
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."

        text += f"{medal} <b>{name}</b> — {count} друзів 🎟\n"

    # 4. Показуємо місце самого користувача (якщо він VIP)
    user_id = message.from_user.id
    user_rank = next((idx for idx, (uid, _) in enumerate(stats) if uid == user_id), None)

    if user_rank is not None:
        my_count = stats[user_rank][1]
        text += f"\n👤 <b>Твоє місце:</b> {user_rank + 1} ({my_count} друзів)"
    else:
        text += "\n👤 Ти не береш участі в рейтингу (потрібен VIP статус)."

    await message.answer(text, parse_mode="HTML")


@router.message(Command("pick_winner"))
async def pick_winner_cmd(message: Message):
    if message.from_user.id != ADMIN_ID:
        return  # Тільки для адміна

    # 1. Отримуємо "барабан" із квитками
    drum = db.get_raffle_participants(days=14)

    if not drum:
        await message.answer("❌ Немає учасників для розіграшу (потрібні VIP користувачі).")
        return

    # 2. Рандомно обираємо переможця
    winner_id = random.choice(drum)

    # 3. Рахуємо статистику для звіту
    total_tickets = len(drum)
    winner_tickets = drum.count(winner_id)
    chance = round((winner_tickets / total_tickets) * 100, 2)

    try:
        # Отримуємо дані переможця
        chat = await message.bot.get_chat(winner_id)
        username = f"@{chat.username}" if chat.username else "немає юзернейму"
        name = chat.first_name or "Користувач"
    except Exception:
        username = "невідомо"
        name = "Користувач"

    # 4. Формуємо повідомлення для адміна з посиланням на профіль
    text = (
        f"🎉 <b>ПЕРЕМОЖЕЦЬ ОБРАНИЙ!</b>\n\n"
        f"👤 <b>Ім'я:</b> {name}\n"
        f"🆔 <b>ID:</b> <code>{winner_id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n"
        f"🎟 <b>Квитків у нього:</b> {winner_tickets}\n"
        f"📈 <b>Шанс був:</b> {chance}%\n\n"
        f"👉 <a href='tg://user?id={winner_id}'>ВІДКРИТИ ПРОФІЛЬ ТА ПОДАРУВАТИ</a>\n\n"
        f"<i>Тепер ти можеш переслати це повідомлення в канал або зробити скріншот!</i>"
    )

    await message.answer(text, parse_mode="HTML")


@router.message(Command("notify_grades"))
@router.message(F.text == "🔔 Оцінки (сповіщення)")
async def turn_notify_grades(message: Message):
    user_id = message.from_user.id
    track_activity(user_id)

    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
    if not is_vip:
        await message.answer("🔒 Сповіщення про оцінки доступні лише VIP-користувачам.\n"
                             "🎁 Хочеш VIP безкоштовно? Переглянь /vip")
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


@router.message(Command("notify"))
@router.message(F.text == "⏰ Нагадування уроків")
async def turn_notify(message: Message):
    user_id = message.from_user.id
    track_activity(user_id)
    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
    if not is_vip:
        await message.answer("🔒 Ця функція доступна лише VIP-користувачам.\n"
                             "🎁 Хочеш VIP безкоштовно? Переглянь /vip")
        return
    else:
        if db.user_notify(user_id):
            db.toggle_notify(user_id)
            await message.answer("❌ Нагадування вимкнені!")
        else:
            db.toggle_notify(user_id)
            await message.answer("✅ Нагадування увімкнені! За 5 хв. до урока вам буде надіслано сповіщення.")
