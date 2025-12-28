import os
import asyncio
import pytz
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from db import DataBase
from cryptography.fernet import Fernet

# Завантажуємо змінні оточення
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
KEY = os.getenv("key")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")
if not KEY:
    raise RuntimeError("key not set")

ADMIN_ID = 1076078800

# Ініціалізація
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = DataBase("data.db")
fernet = Fernet(KEY)

# Глобальні об'єкти
SEMAPHORE = asyncio.Semaphore(3)
RATE_LOCK = asyncio.Lock()
KYIV_TZ = pytz.timezone("Europe/Kiev")

# Кеші
USER_LAST_CALL = {}
SENT_REMINDERS = set()
HW_AI_CACHE = {}
WRAPPED_CACHE = {}
