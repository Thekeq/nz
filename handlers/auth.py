import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from loader import db, fernet, SEMAPHORE
from states import AuthStates
from keyboards import kb_provider, kb_retry
from services.diarynz import get_diary_schedule, InvalidCredentials
from services.diaryhuman import get_diary_schedule_human
from utils import process_referral_reward

router = Router()


@router.message(Command("login"))
async def login(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if db.has_credentials(user_id):
        await message.reply("Спочатку вийдіть з аккаунту /logout")
        return

    await state.set_state(AuthStates.provider)
    await message.answer("Оберіть щоденник:", reply_markup=kb_provider)


@router.message(F.text == "🔑 Увійти у щоденник")
async def login_text(message: Message, state: FSMContext):
    await login(message, state)


@router.message(AuthStates.provider)
async def process_provider(message: Message, state: FSMContext):
    t = (message.text or "").strip()

    if "Human" in t:
        provider = "human"
        prompt = "📧 Введіть email (Human):"
    else:
        provider = "nz"
        prompt = "🔑 Введіть логін (nz.ua):"

    await state.update_data(provider=provider)
    await state.set_state(AuthStates.login)
    await message.answer(prompt, reply_markup=ReplyKeyboardRemove())


@router.message(AuthStates.login)
async def process_login(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Будь ласка, введіть текстом.")
        return

    data = await state.get_data()
    provider = data.get("provider", "nz")

    await state.update_data(login=message.text.strip())
    await state.set_state(AuthStates.password)

    hint = "🔒 Тепер введіть пароль (Human):" if provider == "human" else "🔒 Тепер введіть пароль (nz.ua):"
    await message.answer(hint)


@router.message(AuthStates.password)
async def process_password(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("❌ Пароль потрібно вводити текстом.")
        return

    user_id = message.from_user.id
    data = await state.get_data()

    provider = data.get("provider", "nz")
    login = data["login"]
    password = message.text.strip()

    enc_password = fernet.encrypt(password.encode()).decode()

    try:
        async with SEMAPHORE:
            if provider == "human":
                schedule = await asyncio.to_thread(get_diary_schedule_human, login, password)
            else:
                schedule = await asyncio.to_thread(get_diary_schedule, login, password)

        db.add_user(user_id, login, enc_password, provider=provider)
        if schedule:
            db.set_creds_verified(user_id, 1)
            # Засчитать рефералку ТОЛЬКО после verified и только 1 раз на юзера
            await process_referral_reward(user_id)

        from handlers.common import start
        await start(message, state)

    except InvalidCredentials as e:
        await message.answer(f"❌ {e}", reply_markup=kb_retry)
    except Exception as e:
        print(e)
        await message.answer("❌ Сталася помилка. Спробуйте пізніше.", reply_markup=kb_retry)

    await state.clear()


@router.message(Command('logout'))
async def logout(message: Message):
    user_id = message.from_user.id
    if db.has_credentials(user_id):
        db.delete_user(user_id)  # тепер видаляє тільки креди
        await message.answer("✅ Ви успішно вийшли з акаунту.")
    else:
        await message.answer("⚠️ Ви ще не авторизовані.")


@router.callback_query(lambda c: c.data == "retry_login")
async def retry_login(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    await logout(callback.message)

    await callback.message.answer("🔄 Спробуємо ще раз!\nВведіть свій логін:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AuthStates.login)

    await callback.answer()
