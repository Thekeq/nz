import time
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, \
    InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from loader import db, bot, ADMIN_ID
from keyboards import build_vip_kb, build_main_kb
from states import SupportStates, AuthStates  # Якщо треба
from utils import track_activity, user_can_call

router = Router()


@router.message(CommandStart())
@router.message(F.text == "ℹ️ Головне меню")
async def start(message: Message, state: FSMContext):
    user_id = message.from_user.id

    # завжди гарантуємо рядок у users/subs
    db.ensure_user(user_id)

    start_command = message.text or ""
    param = start_command[7:] if len(start_command) > 7 else ""
    referrer_id = int(param) if param.isdigit() else None

    if referrer_id and referrer_id != user_id:
        has_creds = db.has_credentials(user_id)
        already_has_ref = db.get_referrer(user_id)

        # Рефералку принимаем только для "пустых" (без привязанных creds) и только 1 раз
        if not has_creds and not already_has_ref:
            try:
                ok = db.set_referrer(user_id,
                                     referrer_id)  # должен быть атомарный (UPDATE ... WHERE referrer_id IS NULL)
                if ok:
                    # НИЧЕГО не начисляем тут
                    await bot.send_message(
                        referrer_id,
                        "✅ Друг перейшов за вашим посиланням.\n"
                        "Зарахування буде після підключення ним NZ-акаунту (отримає розклад/оцінки)."
                    )
            except Exception as e:
                print("referral error:", e)

    await message.answer(
        "<b>Використовуючи бот, ви погоджуєтеся з політикою /policy</b>", parse_mode="HTML"
    )

    welcome_text = (
        "👋 Привіт! Я бот для <b>Human.ua, NZ.ua</b>:\n\n"
        "✅ Нагадую про уроки за 5 хв до початку\n"
        "✅ Показую розклад, новини та середній бал\n"
        "✅ Даю бонуси за запрошення друзів\n\n"
        "Щоб показати приклад — натисни кнопку нижче."
    )

    if db.has_credentials(user_id):
        # 1. Текст для VIP користувачів
        vip_text = (
            "👋 <b>Вітаю у головному меню!</b>\n\n"
            "🎓 <b>Навчання:</b>\n"
            "📅 /diary — Розклад уроків\n"
            "📕 /homework — Д/з та поради ШІ\n"
            "📊 /avg_grades — Оцінки, прогнози та рейтинг\n"
            "📰 /news — Шкільні новини\n"
            "⏰ /notify — Нагадування перед уроками\n"
            "🔔 /notify_grades — Сповіщення о нових оцінках (nz)\n\n"
            "🤖 <b>AI та Інструменти:</b>\n"
            "✨ /ai — Чат з розумним помічником\n"
            "🎨 /wrapped — Підсумки тижня у картинках\n"
            "🏆 /leaderboard — Топ учнів та розіграші\n\n"
            "⚙️ <b>Інше:</b>\n"
            "⭐️ /vip — Мій статус та бонуси\n"
            "📜 /policy — Правила користування\n"
            "🔧 /support — Підтримка\n"
            "🚪 /logout — Вихід з акаунту"
        )

        # 2. Текст для звичайних користувачів
        free_text = (
            "👋 <b>Вітаю у головному меню!</b>\n\n"
            "🎓 <b>Навчання:</b>\n"
            "📅 /diary — Розклад уроків\n"
            "📕 /homework — Домашні завдання\n"
            "📊 /avg_grades — Середній бал\n"
            "📰 /news — Шкільні новини\n\n"
            "🚀 <b>Додатково:</b>\n"
            "🎨 /wrapped — Підсумки тижня у картинці\n"
            "⭐️ <b>/vip — Отримати ШІ, аналітику та стилі</b>\n"
            "🏆 /leaderboard — Топ учнів та розіграші\n\n"
            "⚙️ <b>Інше:</b>\n"
            "📜 /policy — Правила користування\n"
            "🔧 /support — Підтримка\n"
            "🚪 /logout — Вихід з акаунту"
        )

        if message.chat.type == "private":
            vip_flag, expires = db.get_vip_status(user_id)
            now_ts = int(time.time())
            is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

            if is_vip:
                await message.answer(vip_text, parse_mode="HTML", reply_markup=build_vip_kb())
            else:
                await message.answer(free_text, parse_mode="HTML", reply_markup=build_main_kb())
        else:
            # У групах не показуємо клавіатуру, тільки текст (скорочений)
            group_text = (
                "🎓 <b>Меню бота:</b>\n"
                "/diary — Розклад\n"
                "/homework — ДЗ\n"
                "/avg_grades — Оцінки\n"
                "/ai — ШІ помічник (VIP)\n"
                "/leaderboard — Рейтинг\n"
                "/logout — Вихід"
            )
            await message.answer(group_text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    else:
        if message.chat.type == "private":
            await message.answer(welcome_text, parse_mode="HTML",
                                 reply_markup=ReplyKeyboardMarkup(
                                     keyboard=[
                                         [KeyboardButton(text="🔍 Показати приклад")],
                                         [KeyboardButton(text="🔑 Увійти у щоденник")],
                                     ],
                                     resize_keyboard=True
                                 ))
        else:
            await message.answer(welcome_text, parse_mode="HTML")
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт")


@router.message(Command('policy'))
@router.message(F.text == "📖 Політика")
async def policy(message: Message):
    policy_text = (
        "🔐 <b>Політика конфіденційності та умови використання</b>\n\n"

        "<b>1. Загальні положення</b>\n"
        "Цей Telegram-бот (далі — «Сервіс») є неофіційним програмним інструментом "
        "для зручного перегляду інформації з освітніх платформ NZ.ua та Human.ua.\n"
        "Сервіс не є афілійованим, пов’язаним або схваленим адміністраціями NZ.ua чи Human.ua.\n\n"

        "<b>2. Прийняття умов</b>\n"
        "Використовуючи Сервіс, користувач підтверджує, що ознайомився з цією Політикою "
        "та погоджується з її умовами. У разі незгоди користувач повинен припинити "
        "використання Сервісу.\n\n"

        "<b>3. Які дані обробляються</b>\n"
        "Сервіс може зберігати та обробляти:\n"
        "• Telegram ID користувача\n"
        "• Логін та пароль від NZ.ua або Human.ua\n"
        "• Обраний тип щоденника (NZ / Human)\n"
        "• Налаштування сповіщень\n"
        "• VIP-статус та термін дії\n"
        "• Дані активності в межах бота (для статистики)\n\n"

        "<b>4. Захист даних</b>\n"
        "Паролі від щоденників зберігаються виключно у зашифрованому вигляді. "
        "Розробник застосовує технічні заходи для захисту інформації, однак не може "
        "гарантувати абсолютну безпеку даних.\n\n"

        "<b>5. Використання даних</b>\n"
        "Дані використовуються лише для:\n"
        "• авторизації у відповідному щоденнику\n"
        "• отримання розкладу, домашніх завдань, оцінок і новин\n"
        "• надання відповідей через AI-асистента\n"
        "• надсилання сповіщень (за бажанням користувача)\n"
        "• роботи VIP-функцій та реферальної системи\n"
        "• внутрішньої аналітики сервісу\n\n"

        "<b>6. Сповіщення</b>\n"
        "Користувач може самостійно вмикати або вимикати сповіщення. "
        "Сповіщення про нові оцінки доступні лише VIP-користувачам. "
        "Сервіс не надсилає сторонню рекламу.\n\n"

        "<b>7. VIP-функції та оплата</b>\n"
        "VIP-доступ відкриває розширені можливості бота. "
        "VIP можна отримати через оплату Telegram Stars або безкоштовно "
        "в межах реферальної програми. "
        "Сервіс не зберігає та не обробляє платіжні дані користувачів.\n\n"

        "<b>8. Використання штучного інтелекту (AI)</b>\n"
        "У роботі Сервісу (для аналізу даних, чату або генерації порад) використовується "
        "модель <b>Gemini 2.5 Flash Lite</b>. Штучний інтелект може помилятися "
        "або надавати неточну інформацію (галюцинувати). "
        "Будь-яка інформація, надана AI, носить довідковий характер і потребує перевірки.\n\n"

        "<b>9. Реферальна система</b>\n"
        "Реферальна система призначена для отримання бонусів у вигляді VIP-доступу. "
        "Нарахування бонусів відбувається лише за реальних користувачів, які "
        "підключили щоденник. Спроби накрутки або зловживання можуть призвести "
        "до скасування бонусів або обмеження доступу.\n\n"

        "<b>10. Обмеження відповідальності</b>\n"
        "Сервіс надається за принципом «як є». Розробник не несе відповідальності за "
        "зміни або збої у роботі NZ.ua чи Human.ua, тимчасову недоступність Сервісу, "
        "помилки в роботі AI-моделі або можливі неточності в даних.\n\n"

        "<b>11. Видалення даних</b>\n"
        "Користувач може у будь-який момент вийти з акаунту через команду /logout "
        "або звернутися до підтримки для повного видалення даних.\n\n"

        "<b>12. Підтримка</b>\n"
        "З усіх питань та пропозицій можна звернутися через команду /support."
    )

    await message.answer(policy_text, parse_mode="HTML")


@router.message(Command('support'))
async def support_start(message: Message, state: FSMContext):
    await message.answer(
        "✉️ Напишіть своє повідомлення (помилку, пропозицію або запит). "
        "Розробник отримає його особисто."
    )
    await state.set_state(SupportStates.waiting_message)


@router.message(SupportStates.waiting_message)
async def support_send(message: Message, state: FSMContext):
    user = message.from_user
    if not message.text:
        await message.answer("❌ Повідомлення потрібно вводити текстом.")
        return
    text = message.text.strip()
    msg = (
        f"📩 <b>Повідомлення від користувача</b>\n"
        f"👤 ID: <code>{user.id}</code>\n"
        f"🔗 @{user.username or 'нема'}\n\n"
        f"<b>Текст:</b>\n{text}"
    )
    await bot.send_message(ADMIN_ID, msg, parse_mode="HTML")

    await message.answer("✅ Повідомлення надіслано розробнику. Дякую!")
    await state.clear()


@router.message(F.text == "🔍 Показати приклад")
async def example_text(message: Message):
    user_id = message.from_user.id
    allowed = await user_can_call(user_id, "example", cooldown=3)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом.")
        return
    demo_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Хочу свій розклад",
                    callback_data="want_my_schedule"
                )
            ]
        ]
    )

    await message.reply(
        "📅 <b>Сьогодні</b>\n"
        "1️⃣ ——\n"
        "2️⃣ Українська мова: https://meet.google.com/aaa-bbb-ccc\n"
        "3️⃣ Фізика: https://meet.google.com/ddd-eee-fff\n"
        "4️⃣ Інформатика: https://meet.google.com/ggg-hhh-iii\n"
        "5️⃣ Інформатика: https://meet.google.com/ggg-hhh-iii\n"
        "6️⃣ Фізична культура: https://meet.google.com/jjj-kkk-lll\n"
        "7️⃣ Українська література: https://meet.google.com/aaa-bbb-ccc\n\n"
        "📅 <b>Завтра</b>\n"
        "1️⃣ Історія України: https://meet.google.com/mmm-nnn-ooo\n"
        "2️⃣ Зарубіжна література: https://meet.google.com/ppp-qqq-rrr\n"
        "3️⃣ Геометрія: https://meet.google.com/xxx-yyy-zzz\n"
        "4️⃣ Хімія: —\n"
        "5️⃣ Біологія і екологія: —\n"
        "6️⃣ Мистецтво: https://meet.google.com/sss-ttt-uuu\n"
        "7️⃣ Фізична культура: —",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=demo_kb
    )


@router.callback_query(F.data == "want_my_schedule")
async def cb_want_my_schedule(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    # мягкий текст без пугающих формулировок
    await callback.message.answer(
        "Щоб показати саме <b>твій</b> розклад з NZ.ua, потрібно один раз увійти у свій акаунт.\n\n"
        "Це займає менше хвилини: Далі бот автоматично підтягує твій розклад, оцінки та новини.",
        parse_mode="HTML"
    )

    # просто прокидываем в уже существующий /login-хендлер
    if not db.has_credentials(callback.from_user.id):
        from handlers.auth import login
        await login(callback.message, state)
    else:
        await callback.message.reply("Спочатку вийдіть з аккаунту /logout")
# ... support_send, example_text, cb_want_my_schedule ...
