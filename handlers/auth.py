import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
import texts
from loader import db, fernet, SEMAPHORE
from states import AuthStates
from keyboards import kb_retry
from services.diarynz import clear_user_session_cache, get_diary_schedule, InvalidCredentials
from utils import process_referral_reward

router = Router()


@router.message(Command("login"))
async def login(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if db.has_credentials(user_id):
        await message.reply("Спочатку вийдіть з аккаунту /logout")
        return

    await state.set_state(AuthStates.login)
    await message.answer("🔑 Введіть логін від Нових Знань (nz.ua):", reply_markup=ReplyKeyboardRemove())


@router.message(F.text.in_({texts.BTN_LOGIN}))
async def login_text(message: Message, state: FSMContext):
    await login(message, state)


@router.message(AuthStates.login)
async def process_login(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Будь ласка, введіть текстом.")
        return

    await state.update_data(login=message.text.strip())
    await state.set_state(AuthStates.password)

    await message.answer("🔒 Тепер введіть пароль від Нових Знань (nz.ua):")


@router.message(AuthStates.password)
async def process_password(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Пароль потрібно вводити текстом.")
        return

    user_id = message.from_user.id
    data = await state.get_data()

    login = data["login"]
    password = message.text.strip()

    enc_password = fernet.encrypt(password.encode()).decode()

    try:
        async with SEMAPHORE:
            schedule = await asyncio.to_thread(
                get_diary_schedule,
                login,
                password,
                user_id=user_id,
                db=db,
                fernet=fernet
            )

        db.add_user(user_id, login, enc_password)
        if schedule:
            db.set_creds_verified(user_id, 1)
            # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
            await process_referral_reward(user_id)

            # Пробний VIP один раз: юзер має відчути нагадування і ШІ,
            # інакше йому нема за що платити
            if db.try_use_trial(user_id):
                db.set_vip(user_id, days=3, source="paid")
                db.set_tokens(user_id, max(db.get_tokens(user_id), 50_000))
                db.record_command_metric("funnel:trial", 0)
                await message.answer(
                    "🎁 <b>Дарую VIP на 3 дні — спробуй все:</b>\n\n"
                    "✨ ШІ-асистент (50k токенів вже на балансі)\n"
                    "🌅 /notify_digest — ранковий дайджест о 7:30\n"
                    "⏰ /notify — нагадування перед уроками з посиланням на мит\n"
                    "🔔 /notify_grades — сповіщення про нові оцінки\n"
                    "🎨 /wrapped — ексклюзивні теми звіту",
                    parse_mode="HTML"
                )

        from handlers.common import start
        await start(message, state)

    except InvalidCredentials as e:
        await message.answer(f"❌ {e}", reply_markup=kb_retry)
    except Exception as e:
        error_text = str(e)  # Перетворюємо помилку в рядок

        if "User not found" in error_text or "password wrong" in error_text:
            await message.answer(
                "⛔️ <b>Невірний логін або пароль!</b>\n\n"
                "Перевірте логін і пароль від nz.ua та спробуйте ще раз.",
                parse_mode="HTML",
                reply_markup=kb_retry
            )

        elif "502" in error_text or "504" in error_text or "Server is busy" in error_text:
            await message.answer(
                "😵 <b>Сайт NZ.ua зараз перевантажений.</b>\n"
                "Спробуйте через 5 хвилин.",
                parse_mode="HTML",
                reply_markup=kb_retry
            )

        # Всі інші невідомі помилки
        else:
            await message.answer("❌ Сталася невідома помилка. Спробуйте пізніше.", reply_markup=kb_retry)

    await state.clear()


@router.message(Command('logout'))
async def logout(message: Message):
    user_id = message.from_user.id
    if db.has_credentials(user_id):
        clear_user_session_cache(user_id)
        db.delete_user(user_id)  # тепер видаляє тільки креди
        await message.answer("✅ Ви успішно вийшли з акаунту.")
    else:
        await message.answer("⚠️ Ви ще не авторизовані.")


@router.callback_query(lambda c: c.data == "retry_login")
async def retry_login(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if db.has_credentials(user_id):
        clear_user_session_cache(user_id)
        db.delete_user(user_id)

    await state.set_state(AuthStates.login)
    await callback.message.answer(
        "🔄 Спробуємо ще раз!\n🔑 Введіть логін від Нових Знань (nz.ua):",
        reply_markup=ReplyKeyboardRemove(),
    )

    await callback.answer()
