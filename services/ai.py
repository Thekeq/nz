import os
import asyncio
import logging

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image
import io

load_dotenv()
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a concise tutor. Always use Ukrainian language as default for all answers.\n"
    "Default: <= 600 chars, but prefer very short answers.\n"
    "Always answer briefly and to the point. Avoid explanations and background unless explicitly asked.\n"
    "No long intros. No unnecessary analysis or commentary.\n"
    "Telegram HTML only. Allowed tags: b, i, u, s, tg-spoiler, blockquote, a, code, pre. All others forbidden.\n"
    "CODE BLOCKS RULE:\n"
    "1. For inline code use: <code>text</code>\n"
    "2. For multiline code use: <pre><code class=\"language-python\">...code...</code></pre>\n"
    "3. NEVER use markdown code blocks.\n"
    "FORBIDDEN CONTENT: Never start answer with '<!DOCTYPE' or '<html>'.\n"
    "CRITICAL RULE: If you write about code, HTML tags, or symbols like '<' or '>', you MUST wrap them in <code> tags (e.g., <code>&lt;div&gt;</code>). Never output raw unsupported tags.\n"
    "All opened tags must be closed. No markdown/latex. If unsure, use plain text.\n"
    "Lists: use '• ' lines, no <ul>/<li>.\n"
    "Math: write as plain text (log_9(81), 9^(-2)); do NOT use <sub>/<sup>.\n"
)

SYSTEM_INSTRUCTION = SYSTEM_PROMPT

class AIUnavailable(Exception):
    """Проблема на нашому боці: немає ключа, вичерпані кредити, впав API.

    Відрізняється від «модель не змогла відповісти»: у цьому випадку
    користувач не винен, і безкоштовний запит йому треба повернути.
    """


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Дефолт мусить бути живою моделлю: gemini-2.5-flash-lite Google уже не видає
# новим користувачам і відповідає 404 із порадою перейти на 3.5. Прод це не
# зачепило лише тому, що там GEMINI_MODEL заданий явно, — тобто поломка чекала
# на першому ж чистому встановленні.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Google відмовляє за геолокацією вихідного IP: з нашого дата-центру
# generateContent повертає 400 FAILED_PRECONDITION «User location is not
# supported», хоча той самий ключ працює з домашнього комп'ютера. Тому запити
# ходять через власний проксі на Cloudflare — назовні вони йдуть з адрес
# Cloudflare, які Google не ріже. Порожній GEMINI_BASE_URL — ходимо напряму.
#
# Секрет — не про ключ (він у проксі не зберігається), а про квоту: воркер
# стоїть у відкритому інтернеті, і без заголовка будь-хто, хто знайде адресу,
# витратить наш безкоштовний ліміт запитів.
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "").rstrip("/")
GEMINI_PROXY_SECRET = os.getenv("GEMINI_PROXY_SECRET", "")

if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY is not set; AI requests will fail until it is configured")


def http_options() -> types.HttpOptions | None:
    """Куди і з чим ходити. Спільне для бота і для deploy/check_ai.py —
    діагностика, що перевіряє не той шлях, гірша за її відсутність."""
    if not GEMINI_BASE_URL:
        return None
    return types.HttpOptions(
        base_url=GEMINI_BASE_URL,
        headers={"x-proxy-secret": GEMINI_PROXY_SECRET} if GEMINI_PROXY_SECRET else None,
    )


client = (genai.Client(api_key=GEMINI_API_KEY, http_options=http_options())
          if GEMINI_API_KEY else None)


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))

    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")

    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70, optimize=True)
    return buf.getvalue()


def _generate_content(contents, max_output_tokens: int):
    if client is None:
        raise AIUnavailable("GEMINI_API_KEY is not configured")

    return client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.2,
            max_output_tokens=max_output_tokens,
        ),
    )


async def ai(user_prompt: str, image_bytes: bytes | None = None) -> tuple[str, int]:
    try:
        # если есть фото → мультимодал
        if image_bytes:
            image_bytes = compress_image(image_bytes)
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part(text=user_prompt),
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/jpeg",  # Telegram почти всегда jpeg
                                data=image_bytes
                            )
                        )
                    ]
                )
            ]
        else:
            # только текст
            contents = [user_prompt]

        response = await asyncio.to_thread(
            _generate_content,
            contents,
            800 if not image_bytes else 1000,
        )
        # --- Отслеживание токенов ---
        # Рахуємо токени
        usage = getattr(response, "usage_metadata", None)
        total_tokens = int(getattr(usage, "total_token_count", 0) or 0)

        text_response = (response.text or "").strip()

        if not text_response:
            # Порожня відповідь без винятку: спрацював фільтр безпеки або
            # модель вичерпала max_output_tokens. Без цього логу користувач
            # бачить «Халепа», а в журналі — тиша.
            candidates = getattr(response, "candidates", None) or []
            logger.warning(
                "Gemini returned empty text: model=%s finish=%s block=%s usage=%s",
                GEMINI_MODEL,
                [getattr(c, "finish_reason", None) for c in candidates],
                getattr(response, "prompt_feedback", None),
                usage,
            )

        # ВАЖЛИВО: Повертаємо кортеж (текст, витрати)
        return text_response, total_tokens

    except AIUnavailable:
        raise
    except Exception as e:
        logger.exception("Gemini request failed: %s", e)

        # Будь-який виняток тут — наша проблема, а не «погане запитання».
        # Випадок, коли користувач справді спитав так, що модель не змогла
        # відповісти, виглядає інакше: успішна відповідь із порожнім текстом,
        # і її розбирає гілка вище.
        #
        # Раніше тут стояв перелік статусів (401/403/429/5xx). Він пропустив
        # 400 FAILED_PRECONDITION «User location is not supported»: бот казав
        # «спробуйте змінити запитання» на відмову Google за геолокацією,
        # спалював безкоштовні запити і жодного разу не розбудив адміна.
        # Перелічувати відомі поломки — готуватись до тієї, що вже сталась.
        raise AIUnavailable(str(e)) from e
