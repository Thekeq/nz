import asyncio
import os
import re
import time
import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, LabeledPrice, \
    PreCheckoutQuery, ShippingQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from db import DataBase
from diarynz import get_diary_schedule, get_diary_grades, get_diary_news
from finder import getsession, getinfo
import pytz

KYIV_TZ = pytz.timezone("Europe/Kiev")
LESSON_TIMES = ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]
LEAD_MIN = 5  # за скільки хвилин нагадати
# Завантажуємо .env
load_dotenv()

SEMAPHORE = asyncio.Semaphore(5)
key = os.getenv("key")
if not key:
    raise RuntimeError("key not set")
fernet = Fernet(key)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

router = Router()
db = DataBase("data.db")
ADMIN_ID = 1076078800
USER_LAST_CALL = {}  # { (user_id, action): last_timestamp }
RATE_LOCK = asyncio.Lock()  # захищає USER_LAST_CALL при конкурентному доступі
DEFAULT_COOLDOWN = 5  # сек — 1 запит кожні 5 секунд


def build_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📅 Розклад"),
                KeyboardButton(text="⭐️ Free VIP"),
                KeyboardButton(text="📖 Політика"),
                KeyboardButton(text="📰 Новини")
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
                KeyboardButton(text="📰 Новини"),
                KeyboardButton(text="🈴 Середній бал")
            ],
            [
                KeyboardButton(text="🔔 Сповіщення"),
                KeyboardButton(text="📖 Політика")
            ]
        ],
        resize_keyboard=True,
        input_field_placeholder="Обери дію…"
    )


async def sleep_to_next_minute():
    now = datetime.datetime.now(tz=KYIV_TZ)
    nxt = (now + datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    await asyncio.sleep((nxt - now).total_seconds())


async def check_lessons(bot):
    while True:
        now = datetime.datetime.now(KYIV_TZ)

        for idx, lesson_time in enumerate(LESSON_TIMES, start=1):  # idx = 1..7
            h, m = map(int, lesson_time.split(":"))
            lesson_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            notify_dt = lesson_dt - datetime.timedelta(minutes=LEAD_MIN)

            # вікно у 60 секунд навколо notify_dt
            if 0 <= (now - notify_dt).total_seconds() < 60:
                users = await asyncio.to_thread(db.get_users_with_notify)  # [(user_id, login, enc_password), ...]

                for user_id, login, enc_password in users:
                    try:
                        vip_flag, expires = await asyncio.to_thread(db.get_vip_status, user_id)
                        now_ts = int(time.time())
                        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                        if not login or not enc_password or not is_vip:
                            continue
                        password = fernet.decrypt(enc_password.encode()).decode()

                        async with SEMAPHORE:
                            schedule_text = await asyncio.to_thread(
                                get_diary_schedule, login, password, days=["сьогодні"]
                            )

                        # Парсимо текст у список уроків
                        lessons_list = []
                        for line in schedule_text.splitlines():
                            m = re.match(r"\d+\.\s*(.*)", line)
                            if m:
                                lessons_list.append(m.group(1).strip())

                        if len(lessons_list) >= idx:
                            lesson_name = lessons_list[idx - 1]  # idx-1 тому що список з 0
                            if "https://meet.google.com" in lesson_name:
                                text = f"🔔 Нагадування: через {LEAD_MIN} хвилин починається <b>{lesson_name}</b> о {lesson_time}."
                                await bot.send_message(user_id, text, parse_mode="HTML", disable_web_page_preview=False)
                                await asyncio.sleep(0.25)

                    except Exception:
                        pass  # просто пропускаємо помилки

        await sleep_to_next_minute()


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


# FSM
class AuthStates(StatesGroup):
    login = State()
    password = State()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    user_id = message.from_user.id

    # завжди гарантуємо рядок у users/subs
    await asyncio.to_thread(db.ensure_user, user_id)

    start_command = message.text or ""
    param = start_command[7:] if len(start_command) > 7 else ""
    referrer_id = int(param) if param.isdigit() else None

    if referrer_id and referrer_id != user_id:
        has_creds = await asyncio.to_thread(db.has_credentials, user_id)
        already_has_ref = await asyncio.to_thread(db.get_referrer, user_id)  # добавь метод, если ещё нет
        if not has_creds and not already_has_ref:
            try:
                ok = await asyncio.to_thread(db.set_referrer, user_id, referrer_id)
                if ok:
                    count_before = await asyncio.to_thread(db.get_invites, referrer_id)
                    await asyncio.to_thread(db.add_invite, referrer_id, 1)
                    count_after = count_before + 1
                    if count_after == 5:
                        await asyncio.to_thread(db.set_vip, referrer_id, 7)
                        await bot.send_message(referrer_id, "Ви отримали ⭐️ ВІП доступ на 7 днів!")
                        await asyncio.to_thread(db.add_invite, referrer_id, -5)  # цикл 5/5
                    else:
                        await bot.send_message(referrer_id,
                                               f"Ви запросили друга, прогрес: {count_after}/5 до безкоштовного VIP")
            except Exception as e:
                print("referral error:", e)

    await message.answer(
        "<b>⚠️ УВАГА!\n</b>"
        "<b>Використовуючи бот, ви погоджуєтеся з політикою /policy</b>", parse_mode="HTML"
    )
    welcome_text = (
        "👋 Привіт! Це бот для зручного доступу до <b>NZ.ua</b>.\n\n"
        "✅ Нагадує про уроки за 5 хв до початку\n"
        "✅ Показує розклад та середній бал\n"
        "✅ Дає бонуси за запрошення друзів\n\n"
        "🔒 Для початку потрібно увійти у свій акаунт NZ."
    )

    if db.has_credentials(user_id):
        if message.chat.type == "private":
            vip_flag, expires = db.get_vip_status(user_id)
            now_ts = int(time.time())
            is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
            if is_vip:
                await message.answer("/diary - Переглянути розклад\n"
                                     "/avg_grades - Переглянути середній бал\n"
                                     "/vip - Переглянути ВІП-Функції\n"
                                     "/policy - Переглянути політику користувача\n"
                                     "/support - Надіслати повідомлення підтримці\n"
                                     "/logout - Вийти з акаунту", reply_markup=build_vip_kb())
            else:
                await message.answer("/diary - Переглянути розклад\n"
                                     "/avg_grades - Переглянути середній бал\n"
                                     "/vip - Переглянути ВІП-Функції\n"
                                     "/policy - Переглянути політику користувача\n"
                                     "/support - Надіслати повідомлення підтримці\n"
                                     "/logout - Вийти з акаунту", reply_markup=build_main_kb())
        else:
            await message.answer("/diary - Переглянути розклад\n"
                                 "/avg_grades - Переглянути середній бал\n"
                                 "/vip - Переглянути ВІП-Функції\n"
                                 "/policy - Переглянути політику користувача\n"
                                 "/support - Надіслати повідомлення підтримці\n"
                                 "/logout - Вийти з акаунту")
    else:
        if message.chat.type == "private":
            await message.answer(welcome_text, parse_mode="HTML")
            await state.set_state(AuthStates.login)
            await message.answer("Щоб користуватись функціями бота потрібно увійти у аккаунт NZ\n"
                                 "👤 Введіть свій логін:", reply_markup=ReplyKeyboardRemove())
        else:
            await message.answer(welcome_text, parse_mode="HTML")
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт",
                reply_markup=ReplyKeyboardRemove())


@router.message(Command('rm'))
async def rm(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.reply('✅', reply_markup=ReplyKeyboardRemove())


@router.message(AuthStates.login)
async def process_login(message: Message, state: FSMContext):
    await state.update_data(login=message.text.strip())
    await state.set_state(AuthStates.password)
    await message.answer("🔒 Тепер введіть пароль:")


@router.message(AuthStates.password)
async def process_password(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()

    login = data["login"]
    password = message.text.strip()

    enc_password = fernet.encrypt(password.encode()).decode()

    try:
        async with SEMAPHORE:
            schedule = await asyncio.to_thread(get_diary_schedule, login, password)
        db.add_user(user_id, login, enc_password)
        await message.answer(f"✅ Дані збережено!")
        await message.answer(f"{schedule}")
        vip_flag, expires = db.get_vip_status(user_id)
        now_ts = int(time.time())
        is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
        if is_vip:
            await message.answer("Головне меню:", reply_markup=build_vip_kb())
        else:
            await message.answer("Головне меню:", reply_markup=build_main_kb())
    except Exception as e:
        await message.answer(f"❌ Помилка при отриманні даних: {e}")

    await state.clear()


@router.message(Command('diary'))
async def get_diary(message: Message, state: FSMContext):
    user_id = message.from_user.id

    allowed = await user_can_call(user_id, "diary", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    if db.has_credentials(user_id):
        try:
            login, enc_password = await asyncio.to_thread(db.get_user, user_id)
            password = fernet.decrypt(enc_password.encode()).decode()

            async with SEMAPHORE:
                schedule = await asyncio.to_thread(get_diary_schedule, login, password)
            if message.chat.type == "private":
                vip_flag, expires = db.get_vip_status(user_id)
                now_ts = int(time.time())
                is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
                if is_vip:
                    await message.answer(f"{schedule}", reply_markup=build_vip_kb())
                else:
                    await message.answer(f"{schedule}", reply_markup=build_main_kb())
            else:
                await message.answer(f"{schedule}")
        except Exception as e:
            await message.answer(f"❌ Помилка при отриманні даних: {e}")
    else:
        if message.chat.type == "private":
            await state.set_state(AuthStates.login)
            await message.answer("👤 Введіть свій логін:")
        else:
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт")


@router.message(Command('news'))
async def news_command(message: Message, state: FSMContext):
    user_id = message.from_user.id

    # антиспам
    allowed = await user_can_call(user_id, "news", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    # потрібен логін NZ
    if not db.has_credentials(user_id):
        if message.chat.type == "private":
            await state.set_state(AuthStates.login)
            await message.answer("👤 Введіть свій логін:")
        else:
            await message.reply("Щоб почати, напишіть боту в особисті повідомлення /start та увійдіть у свій акаунт")
        return

    try:
        login, enc_password = await asyncio.to_thread(db.get_user, user_id)
        if not login or not enc_password:
            await message.answer("❌ У базі немає ваших облікових даних. Виконайте /start заново.")
            return
        password = fernet.decrypt(enc_password.encode()).decode()

        async with SEMAPHORE:
            text = await asyncio.to_thread(get_diary_news, login, password, 10)

        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Помилка при отриманні новин: {e}")


@router.message(F.text == "📅 Розклад")
async def kb_diary(message: Message, state: FSMContext):
    await get_diary(message, state)


@router.message(F.text == "📰 Новини")
async def kb_news(message: Message, state: FSMContext):
    await news_command(message, state)


@router.message(F.text == "🈴 Середній бал")
async def kb_avg_grades(message: Message, state: FSMContext):
    await get_grades(message, state)


@router.message(F.text == "🔔 Сповіщення")
async def kb_notify(message: Message, state: FSMContext):
    await turn_notify(message)


@router.message(F.text == "⭐️ Free VIP")
async def kb_free_vip(message: Message):
    user_id = message.from_user.id
    await message.answer(
        "🎁 Безкоштовний VIP: запросіть 5 друзів і отримайте 7 днів VIP!\n"
        f"Ваше реферальне посилання: https://t.me/nzdiary_bot?start={user_id}"
    )


@router.message(F.text == "📖 Політика")
async def kb_policy(message: Message):
    await policy(message)


@router.message(Command("gift_vip"))
async def gift_vip(message: Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:

        if len(message.text.split()) != 3:
            await message.answer("Формат: /gift_vip <user_id> <days>")
            return

        _, target_id_str, days_str = message.text.split()
        reply_id = int(target_id_str)
        days = int(days_str)

        # set_vip сам ensure_user зробить; не вимагаємо кредів
        db.set_vip(reply_id, days=days)
        vip, expires = db.get_vip_status(reply_id)
        date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y %H:%M")

        await message.answer(f"✅ Видано VIP доступ до {date_str}")
        try:
            await bot.send_message(reply_id, f"🎉 Ви отримали VIP доступ до {date_str}", reply_markup=build_vip_kb())
        except Exception:
            pass


@router.message(Command("notify"))
async def turn_notify(message: Message):
    user_id = message.from_user.id
    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
    if not is_vip:
        await message.answer("🔒 Ця функція доступна лише VIP-користувачам.")
        return
    else:
        if db.user_notify(user_id):
            db.toggle_notify(user_id)
            await message.answer("❌ Повідомлення вимкнені!")
        else:
            db.toggle_notify(user_id)
            await message.answer("✅ Повідомлення увімкнені! За 5 хв. до урока вам буде надіслано сповіщення.")


# canonical day names in ukrainian lowercase (match what get_diary_schedule expects)
CANONICAL_DAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця"]


@router.message(Command('diary_days'))
async def diary_days_command(message: Message):
    user_id = message.from_user.id

    if not db.has_credentials(user_id):
        await message.answer("❌ Ви ще не авторизовані. Використайте /start для входу.")
        return

    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)
    if not is_vip:
        await message.answer("🔒 Ця функція доступна лише VIP-користувачам.")
        return

    today_idx = datetime.datetime.now(KYIV_TZ).weekday()  # tz-aware

    rows = []
    row = []

    for i, canon_day in enumerate(CANONICAL_DAYS):
        if i == today_idx and i < len(CANONICAL_DAYS):
            text = "Сьогодні"
            cb_day = "сьогодні"
        elif i == (today_idx + 1) % len(CANONICAL_DAYS):
            text = "Завтра"
            cb_day = "завтра"
        else:
            text = canon_day.capitalize()
            cb_day = canon_day

        row.append(InlineKeyboardButton(text=text, callback_data=f"diary_day:{cb_day}"))
        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer("📅 Виберіть день (VIP):", reply_markup=keyboard)


@router.callback_query(lambda c: c.data and c.data.startswith("diary_day:"))
async def diary_day_selected(callback: CallbackQuery):
    await callback.answer()

    user_id = callback.from_user.id
    if not db.has_credentials(user_id):
        await callback.message.answer("❌ Ви ще не авторизовані. Використайте /start для входу.")
        return

    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    if not (bool(vip_flag) and (expires == 0 or expires > now_ts)):
        await callback.message.answer("🔒 Ваша VIP-підписка недійсна.")
        return

    day = callback.data.split(":", 1)[1]

    login, enc_password = await asyncio.to_thread(db.get_user, user_id)
    if not login:
        await callback.message.answer("❌ Не знайдено ваш акаунт з логіном у базі.")
        return
    password = fernet.decrypt(enc_password.encode()).decode()

    try:
        async with SEMAPHORE:
            schedule = await asyncio.to_thread(get_diary_schedule, login, password, days=[day])
    except Exception as e:
        await callback.message.answer(f"❌ Помилка при отриманні розкладу: {e}")
        return

    if not schedule:
        await callback.message.answer(f"📅 Розклад на {day} не знайдено або сталася помилка.")
    else:
        await callback.message.answer(f"{schedule}")


@router.message(Command('avg_grades'))
async def get_grades(message: Message, state: FSMContext):
    user_id = message.from_user.id

    allowed = await user_can_call(user_id, "avg_grades", cooldown=5)
    if not allowed:
        await message.answer("⏳ Погоди кілька секунд перед наступним запитом — не перевантажуй сайт.")
        return

    if db.has_credentials(user_id):
        try:
            login, enc_password = await asyncio.to_thread(db.get_user, user_id)
            password = fernet.decrypt(enc_password.encode()).decode()
            async with SEMAPHORE:
                grades, text = await asyncio.to_thread(get_diary_grades, login, password)

            await message.answer(f"{text}")
        except Exception as e:
            await message.answer(f"❌ Помилка при отриманні даних: {e}")
    else:
        if message.chat.type == "private":
            await state.set_state(AuthStates.login)
            await message.answer("👤 Введіть свій логін:")
        else:
            await message.reply(
                "Щоб почати, надішліть боту особисте повідомлення командою /start та увійдіть у свій акаунт")


@router.message(Command('logout'))
async def logout(message: Message):
    user_id = message.from_user.id
    if db.has_credentials(user_id):
        db.delete_user(user_id)  # тепер видаляє тільки креди
        await message.answer("✅ Ви успішно вийшли з акаунту.")
    else:
        await message.answer("⚠️ Ви ще не авторизовані.")


@router.message(Command('policy'))
async def policy(message: Message):
    policy_text = (
        "<b>Політика конфіденційності та умови використання</b>\n\n"
        "1. <b>Про бот</b>\n"
        "Цей бот — неофіційний сервіс для зручного користування NZ.ua.\n"
        "Він не пов'язаний з адміністрацією NZ.ua і створений лише для особистого використання.\n\n"
        "2. <b>Які дані зберігаються</b>\n"
        "- Логін від NZ.ua (текст)\n"
        "- Пароль від NZ.ua (зберігається <b>зашифрованим</b> через Fernet)\n"
        "- Ідентифікатор Telegram (user_id)\n"
        "- Налаштування користувача (сповіщення, VIP, реферали)\n"
        "- Статус ВІП та дата закінчення підписки\n\n"
        "3. <b>Зберігання та безпека</b>\n"
        "- Дані зберігаються у локальній базі SQLite на сервері бота\n"
        "- Паролі зберігаються лише у зашифрованому вигляді (Fernet)\n"
        "- Ключ шифрування зберігається у середовищі (.env)\n"
        "- Дані не передаються третім особам та не використовуються для реклами\n\n"
        "4. <b>Для чого використовуються дані</b>\n"
        "- Для входу на NZ.ua та отримання розкладу, оцінок, нагадувань\n"
        "- Для відправки персональних сповіщень, якщо ви їх увімкнули\n"
        "- Для роботи реферальної системи та VIP-функцій\n\n"
        "5. <b>Оплати</b>\n"
        "- Оплати виконуються офіційно через Telegram Stars\n"
        "- Платіжні дані (картки, акаунти) не зберігаються у боті\n"
        "- Після успішної оплати VIP активується автоматично\n\n"
        "6. <b>Реферальна система</b>\n"
        "- Запрошення друзів нараховує безкоштовні дні VIP\n"
        "- Жодних додаткових персональних даних вона не збирає\n\n"
        "7. <b>Про парсинг</b>\n"
        "- Бот отримує інформацію з NZ.ua через запити (web-scraping)\n"
        "- Можливі тимчасові помилки при зміні структури сайту NZ.ua\n\n"
        "8. <b>Відповідальність</b>\n"
        "- Розробник не несе відповідальності за роботу сайту NZ.ua\n\n"
        "9. <b>Видалення даних</b>\n"
        "- Команда /logout повністю видаляє ваш логін і пароль з бази\n"
        "- Можна звернутися через /support для ручного видалення даних\n"
        "- Видалені дані не підлягають відновленню\n\n"
        "10. <b>Контакти</b>\n"
        "Питання або підтримка — через /support у боті\n\n"
        "<i>Використовуючи цього бота, ви погоджуєтеся з політикою конфіденційності.</i>"
    )

    await message.answer(policy_text, parse_mode="HTML")


def payment_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Отримати VIP за 49 ⭐️", pay=True)
    return builder.as_markup()


@router.message(Command('vip'))
async def vip_func(message: Message):
    user_id = message.from_user.id
    # Дозволяємо відкривати VIP-меню навіть без кредів (оплата не залежить від NZ)
    await asyncio.to_thread(db.ensure_user, user_id)

    vip_flag, expires = db.get_vip_status(user_id)
    now_ts = int(time.time())
    is_vip = bool(vip_flag) and (expires == 0 or expires > now_ts)

    if is_vip:
        date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y")
        await message.answer(f"⭐️ Ви вже маєте VIP до <b>{date_str}</b>\n"
                             f"/diary_days - розклад занять по дням\n"
                             f"/notify - Увімкнути нагадування за 5-хв до уроку\n\n"
                             f"<b>БЕЗКОШТОВНИЙ</b> ⭐️ VIP на 7 днів якщо ви запросите 5 друзів\n"
                             f"Ваше реферальне посилання: https://t.me/nzdiary_bot?start={user_id}\n",
                             parse_mode="HTML")
    else:
        await message.answer(f"⭐️ Перелік VIP-Функцій\n"
                             f"/diary_days - розклад занять по дням\n"
                             f"/notify - Увімкнути нагадування за 5-хв до уроку\n"
                             f"У розробці...\n\n"
                             f"<b>БЕЗКОШТОВНИЙ</b> ⭐️ VIP на 7 днів якщо ви запросите 5 друзів\n"
                             f"Ваше реферальне посилання: https://t.me/nzdiary_bot?start={user_id}\n",
                             parse_mode="HTML")
        prices = [LabeledPrice(label="VIP доступ на 1 місяць", amount=49)]  # сума в XTR-центах
        await message.answer_invoice(
            title="VIP Доступ",
            description="Відкрийте для себе VIP 💎 на 30 днів.",
            payload=f"vip_{user_id}_{int(datetime.datetime.now().timestamp())}",
            provider_token="",  # токен від Telegram Payments (Stars)
            currency="XTR",
            prices=prices,
            start_parameter="vip_payment",
            reply_markup=payment_keyboard()
        )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    await pre_checkout_q.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    user_id = message.from_user.id
    db.set_vip(user_id, days=30)
    vip, expires = db.get_vip_status(user_id)
    date_str = datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y %H:%M")
    await message.answer(f"✅ Ви отримали VIP доступ до {date_str}")


# 👇 FSM для підтримки
class SupportStates(StatesGroup):
    waiting_message = State()


@router.message(Command('support'))
async def support_start(message: Message, state: FSMContext):
    await message.answer(
        "✉️ Напишіть своє повідомлення (помилку, пропозицію або запит). "
        "Розробник отримає його особисто."
    )
    await state.set_state(SupportStates.waiting_message)


@router.message(Command("bc"))
async def broadcast(message: Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return

    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.answer("⚠️ Використання: /bc <текст повідомлення>")
        return

    text = args[1]

    users = await asyncio.to_thread(db.get_all_users)
    count, failed = 0, 0

    for (uid,) in users:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            continue

    await message.answer(f"✅ Розсилка завершена!\n📨 Успішно: {count}\n❌ Не вдалося: {failed}")


@router.message(SupportStates.waiting_message)
async def support_send(message: Message, state: FSMContext):
    user = message.from_user
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


@router.message(F.text.startswith("https://naurok.com.ua/test"))
async def test(message: Message):
    url = message.text
    if url.startswith('https://naurok.com.ua/test/testing/'):
        try:
            url = re.sub(r'/test/testing/', '/test/realtime-client/', url)
        except Exception as e:
            print(e)
    data = getsession(url)
    info = getinfo(data)
    await message.answer(f"Назва тесту: <i>{info}</i>", parse_mode="HTML")


async def main():
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    print("Telegram Bot is running...")
    asyncio.create_task(check_lessons(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
