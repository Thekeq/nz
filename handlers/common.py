import time
import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, \
    InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
import texts
from loader import db, bot, ADMIN_ID
from keyboards import build_vip_kb, build_main_kb
from states import SupportStates, AuthStates  # Якщо треба
from utils import track_activity, user_can_call, safe_send

router = Router()
logger = logging.getLogger(__name__)
POLICY_NOTE = "\n\n<i>Використовуючи бот, ви погоджуєтеся з /policy.</i>"


@router.message(CommandStart())
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
                    await safe_send(
                        referrer_id,
                        "✅ Друг перейшов за вашим посиланням.\n"
                        "Зарахування буде після підключення ним NZ-акаунту (отримає розклад/оцінки)."
                    )
            except Exception:
                logger.exception("Failed to set referral user_id=%s referrer_id=%s", user_id, referrer_id)

    welcome_text = (
        "🎓 <b>Шкільний помічник у Telegram</b>\n\n"
        "• Розклад, ДЗ, оцінки та новини з <b>NZ.ua/Human</b>\n"
        "• Нагадування за 5 хв до уроку\n"
        "• Бонуси за запрошення друзів\n\n"
        "Можеш подивитися приклад або одразу увійти у щоденник."
    ) + POLICY_NOTE

    if db.has_credentials(user_id):
        if message.chat.type == "private":
            vip_flag, expires = db.get_vip_status(user_id)
            now_ts = int(time.time())
            is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

            # Клавіатура — для щоденних дій, повний список команд — у /help.
            # Раніше тут дублювались обидві навігації одночасно.
            await message.answer(
                "👋 <b>Головне меню</b>\n\n"
                "Обирай дію на клавіатурі нижче ↓\n"
                "Усі команди — /help",
                parse_mode="HTML",
                reply_markup=build_vip_kb() if is_vip else build_main_kb()
            )
        else:
            await message.answer(
                "🎓 <b>Бот працює в особистих повідомленнях</b>\n"
                "Команди: /diary, /homework, /avg_grades, /help",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove()
            )
    else:
        if message.chat.type == "private":
            await message.answer(welcome_text, parse_mode="HTML",
                                 reply_markup=ReplyKeyboardMarkup(
                                     keyboard=[
                                         [KeyboardButton(text=texts.BTN_EXAMPLE)],
                                         [KeyboardButton(text=texts.BTN_LOGIN)],
                                     ],
                                     resize_keyboard=True
                                 ))
        else:
            await message.answer(welcome_text, parse_mode="HTML")
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт")


@router.message(Command('help'))
@router.message(F.text.in_(texts.HELP_LABELS))
async def help_cmd(message: Message):
    """Повний список команд. Емодзі — тільки на розділах:
    коли підсвічено все, не підсвічено ніщо."""
    vip_flag, expires = db.get_vip_status(message.from_user.id)
    is_vip = bool(vip_flag) and (expires == 0 or expires > int(time.time()))

    text = (
        "📚 <b>Навчання</b>\n"
        "/diary — розклад уроків\n"
        f"/homework — {texts.HOMEWORK_WORD} та порада ШІ\n"
        "/avg_grades — оцінки та аналітика\n"
        "/news — шкільні новини\n\n"

        "🔔 <b>Сповіщення</b>\n"
        "/notify — перед уроками ⭐️\n"
        "/notify_grades — нові оцінки, NZ ⭐️\n"
        f"/notify_homework — нове {texts.HOMEWORK_WORD}, NZ ⭐️\n"
        "Ранковий дайджест о 7:30 — щодня ⭐️, без VIP по понеділках\n\n"

        "✨ <b>Інструменти</b>\n"
        "/ai — чат з ШІ\n"
        "/wrapped — підсумки тижня картинкою\n"
        "/leaderboard — топ амбасадорів\n\n"

        "⚙️ <b>Акаунт</b>\n"
        "/vip — статус, тарифи, бонуси\n"
        "/support — підтримка\n"
        "/policy — правила\n"
        "/logout — вихід\n\n"

        "<i>⭐️ — тільки для VIP</i>"
    )
    if not is_vip:
        text += "\n<i>Отримати VIP безкоштовно за друга — /vip</i>"

    await message.answer(text, parse_mode="HTML")


@router.message(Command('policy'))
@router.message(F.text.in_(texts.POLICY_LABELS))
async def policy(message: Message):
    policy_text = (
        "🔐 <b>Політика конфіденційності та умови використання</b>\n"
        "<i>Оновлено: 10.08.2026</i>\n\n"

        "<b>1. Загальні положення</b>\n"
        "Цей Telegram-бот (далі — «Сервіс») є неофіційним інструментом для зручного "
        "перегляду інформації з освітніх платформ NZ.ua та Human.ua.\n"
        "Сервіс не афілійований з NZ.ua чи Human.ua і не схвалений ними.\n\n"

        "<b>2. Прийняття умов</b>\n"
        "Використовуючи Сервіс, ви підтверджуєте, що ознайомилися з цією Політикою "
        "та погоджуєтесь з нею. Якщо ви не згодні — припиніть використання Сервісу "
        "та виконайте /logout.\n\n"

        "<b>3. Які дані обробляються</b>\n"
        "• Telegram ID (і ваше імʼя/username — коли ви пишете в /support або "
        "потрапляєте в /leaderboard)\n"
        "• Логін і пароль від NZ.ua або Human.ua\n"
        "• Тип щоденника (NZ / Human)\n"
        "• Сесійні cookie щоденника — щоб не входити повторно при кожному запиті\n"
        "• Налаштування сповіщень і ранкового дайджесту\n"
        "• VIP-статус, термін дії, спосіб отримання (оплата / реферал), баланс ШІ-токенів\n"
        "• Кількість дій по днях і час виконання команд — для статистики та діагностики\n"
        "• Реферальні звʼязки (хто кого запросив)\n"
        "• Тимчасовий стан діалогу (наприклад, крок авторизації) — щоб бот не «губив» "
        "вас після перезапуску\n\n"

        "<b>4. Захист даних</b>\n"
        "Паролі щоденників і сесійні cookie зберігаються <b>лише у зашифрованому вигляді</b> "
        "(AES через Fernet). База розташована на приватному сервері розробника, "
        "її резервні копії створюються щодня і зберігаються там же. "
        "Розробник вживає технічних заходів захисту, але не може гарантувати "
        "абсолютну безпеку.\n\n"

        "<b>5. Для чого використовуються дані</b>\n"
        "• вхід у ваш щоденник і отримання розкладу, ДЗ, оцінок, новин\n"
        "• надсилання сповіщень і ранкового дайджесту (за вашим бажанням)\n"
        "• робота ШІ-асистента\n"
        "• VIP-функції, оплата, реферальна програма, розіграші\n"
        "• внутрішня аналітика та пошук помилок\n"
        "Дані <b>не продаються</b> і не передаються третім особам, крім сервісів, "
        "перелічених у пункті 6.\n\n"

        "<b>6. Передача даних третім сервісам</b>\n"
        "Для окремих функцій частина даних передається зовнішнім сервісам:\n"
        "• <b>NZ.ua / Human.ua</b> — ваш логін і пароль, щоб отримати ваші ж дані\n"
        "• <b>Google Gemini</b> (модель <code>gemini-2.5-flash-lite</code>) — текст або фото, "
        "які ви надсилаєте в /ai, а також текст ДЗ, якщо ви натискаєте «Порада від ШІ»\n"
        "• <b>QuickChart.io</b> — назви предметів і середні бали, щоб намалювати "
        "діаграму у /avg_grades (без вашого імені та ID)\n"
        "• <b>Telegram</b> — усі повідомлення проходять через його інфраструктуру, "
        "оплата обробляється Telegram Stars\n\n"

        "<b>7. Сповіщення</b>\n"
        "Ви самостійно вмикаєте та вимикаєте: /notify (нагадування перед уроками), "
        "/notify_grades (нові оцінки), /notify_homework (нове ДЗ). "
        "Ранковий дайджест о 7:30 надсилається VIP щодня, іншим — раз на тиждень. "
        "Сервіс може надсилати рідкі службові та інформаційні повідомлення "
        "(зміни в роботі, розіграші). Сторонньої рекламы немає.\n"
        "Якщо ви заблокуєте бота, розсилки автоматично вимикаються.\n\n"

        "<b>8. VIP, оплата та повернення</b>\n"
        "VIP можна отримати: оплатою Telegram Stars, переказом на Monobank-банку "
        "(з вашим ID у коментарі), безкоштовним пробним періодом (3 дні, один раз) "
        "або за реферальну програму. "
        "Сервіс <b>не зберігає платіжних даних</b> — оплату повністю обробляє Telegram.\n"
        "Кошти за Telegram Stars повертаються за правилами Telegram. "
        "Оплата не гарантує безперебійної роботи, якщо NZ.ua або Human.ua недоступні.\n\n"

        "<b>9. Реферальна програма</b>\n"
        "За кожного друга, який реально підключив щоденник, ви отримуєте 3 дні VIP "
        "(до 9 днів на місяць). Реферальний VIP не включає ШІ-токени та ексклюзивні "
        "теми звіту. Накрутка фейковими акаунтами веде до скасування бонусів "
        "та обмеження доступу.\n\n"

        "<b>10. Штучний інтелект</b>\n"
        "ШІ може помилятися або вигадувати (галюцинувати). Його відповіді — "
        "довідкові, їх треба перевіряти. Не покладайтесь на ШІ у важливих "
        "навчальних рішеннях. ШІ-функції обмежені токенами: у VIP — 1 000 000, "
        "без VIP — кілька безкоштовних запитів на тиждень.\n\n"

        "<b>11. Обмеження відповідальності</b>\n"
        "Сервіс надається «як є». Розробник не відповідає за: зміни чи збої NZ.ua "
        "та Human.ua, тимчасову недоступність бота, помилки ШІ, неточності або "
        "затримки в даних (зокрема пропущені нагадування, якщо вчитель додав "
        "посилання надто пізно).\n\n"

        "<b>12. Ваші права та видалення даних</b>\n"
        "• /logout — видаляє логін, пароль і сесійні cookie (VIP залишається)\n"
        "• /support — можна попросити повне видалення всіх даних, включно з "
        "активністю та реферальними звʼязками\n"
        "Дані активності зберігаються, поки ви користуєтесь ботом; "
        "резервні копії — до 7 днів.\n\n"

        "<b>13. Вік користувачів</b>\n"
        "Сервіс призначений для школярів. Якщо вам менше 14 років, "
        "використовуйте його з дозволу батьків або опікунів.\n\n"

        "<b>14. Зміни політики</b>\n"
        "Політика може оновлюватися. Актуальна версія завжди доступна за /policy. "
        "Про суттєві зміни розробник повідомить у боті.\n\n"

        "<b>15. Підтримка</b>\n"
        "З усіх питань — /support."
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


@router.message(F.text.in_({texts.BTN_EXAMPLE}))
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
