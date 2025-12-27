import os

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image
import io

load_dotenv()

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

# The client gets the API key from the environment variable `GEMINI_API_KEY`.
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


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


async def ai(user_prompt: str, image_bytes: bytes | None = None) -> str:
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

        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2,
                max_output_tokens=800 if not image_bytes else 1000,
            ),
        )
        # --- Отслеживание токенов ---
        # Рахуємо токени
        total_tokens = response.usage_metadata.total_token_count

        text_response = (response.text or "").strip()

        # ВАЖЛИВО: Повертаємо кортеж (текст, витрати)
        return text_response, total_tokens

    except Exception as e:
        print(e)
        return ""
