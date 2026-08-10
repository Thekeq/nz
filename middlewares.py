import time
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


BUTTON_LABELS = {
    "Розклад": "diary",
    "Д/з": "homework",
    "Новини": "news",
    "Статистика": "grades",
    "Free VIP": "vip",
    "Головне меню": "menu",
    "Увійти": "login",
    "Показати приклад": "example",
    "ШІ": "ai",
    "Нагадування": "notify_lessons",
    "Оцінки": "notify_grades",
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
