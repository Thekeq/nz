import time
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from loader import CHANNEL_ID, CHANNEL_URL, bot

logger = logging.getLogger(__name__)


def is_active_channel_member(member) -> bool:
    """Return whether a Telegram chat member still belongs to the channel."""
    if member.status in {"member", "administrator", "creator"}:
        return True
    return member.status == "restricted" and bool(getattr(member, "is_member", False))


def channel_subscription_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Підписатися на канал", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я підписався", callback_data="check_sub")],
        ]
    )


class ChannelSubscriptionMiddleware(BaseMiddleware):
    """Allow bot interactions only to users subscribed to the NZ channel."""

    async def __call__(self, handler, event, data):
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        # This callback is the only action available before subscription:
        # it performs the membership check and unlocks the bot afterwards.
        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)

        user = event.from_user
        try:
            member = await bot.get_chat_member(CHANNEL_ID, user.id)
        except Exception:
            logger.exception("get_chat_member failed for user_id=%s", user.id)
            await self._deny(event, "⚠️ Не вдалося перевірити підписку. Спробуй ще раз пізніше.")
            return None

        if not is_active_channel_member(member):
            await self._deny(
                event,
                "🔒 Щоб користуватися ботом, спочатку підпишись на канал "
                "«Нові Знання».",
            )
            return None

        return await handler(event, data)

    @staticmethod
    async def _deny(event, text: str):
        markup = channel_subscription_kb()
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
            if event.message:
                await event.message.answer(
                    "Підпишись на канал і натисни «Я підписався» ще раз.",
                    reply_markup=markup,
                )
        else:
            await event.answer(text, reply_markup=markup)


BUTTON_LABELS = {
    "Розклад": "diary",
    "Д/з": "homework",       # стара підпис кнопки — досі приходить з кешованих клавіатур
    "ДЗ": "homework",
    "Новини": "news",
    "Статистика": "grades",  # стара
    "Оцінки (сповіщення)": "notify_grades",
    "Оцінки": "grades",
    "Free VIP": "vip",
    "VIP": "vip",
    "Головне меню": "help",  # стара
    "Довідка": "help",
    "Увійти": "login",
    "Показати приклад": "example",
    "ШІ": "ai",
    "Нагадування": "notify_lessons",
}


def _message_label(message: Message) -> str:
    text = (message.text or "").strip()
    if not text:
        return "message:non_text"

    if text.startswith("/"):
        return text.split(maxsplit=1)[0][:80]

    for needle, label in BUTTON_LABELS.items():
        if needle in text:
            return f"button:{label}"

    return "message:text"


def _callback_label(callback: CallbackQuery) -> str:
    data = callback.data or "unknown"
    prefix = data.split(":", 1)[0]
    return f"callback:{prefix[:60]}"


class MetricsMiddleware(BaseMiddleware):
    def __init__(self, db):
        self.db = db

    async def __call__(
            self,
            handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
            event: Any,
            data: dict[str, Any],
    ) -> Any:
        started = time.perf_counter()
        ok = True

        if isinstance(event, Message):
            label = _message_label(event)
        elif isinstance(event, CallbackQuery):
            label = _callback_label(event)
        else:
            label = event.__class__.__name__

        try:
            return await handler(event, data)
        except Exception:
            ok = False
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            try:
                self.db.record_command_metric(label, elapsed_ms, ok=ok)
            except Exception:
                logger.exception("Failed to record command metric label=%s", label)
