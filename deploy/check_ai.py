"""Діагностика Gemini: запускати на сервері, коли бот відповідає «Халепа».

    cd /opt/nz/app && sudo -u nzbot /opt/nz/venv/bin/python deploy/check_ai.py

Друкує реальну помилку замість того, щоб ковтати її, як це робить ai().
Ключ не виводиться — тільки факт наявності й довжина.

Клієнта беремо той самий, що й у бота. Діагностика, яка ходить іншим шляхом
(скажімо, повз проксі), скаже «все добре» там, де бот падає, — це гірше, ніж
не мати діагностики зовсім.
"""
import os
import sys

# запуск із теки deploy/, тому корінь проєкту додаємо в шлях самі
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from google.genai import types
    from services.ai import GEMINI_BASE_URL, GEMINI_MODEL, client
except ImportError as e:
    print(f"Не імпортується services.ai (пакети у venv?): {e}")
    sys.exit(1)

key = os.getenv("GEMINI_API_KEY") or ""

print(f"GEMINI_API_KEY: {'є' if key else 'НЕМАЄ'} (довжина {len(key)})")
print(f"GEMINI_MODEL:   {GEMINI_MODEL}")
print(f"через проксі:   {GEMINI_BASE_URL or 'ні, напряму до Google'}")
print(f"cwd:            {os.getcwd()}")

if client is None:
    print("\n.env не містить GEMINI_API_KEY або лежить не в робочій теці.")
    sys.exit(1)

try:
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=["Скажи одним словом: працює?"],
        config=types.GenerateContentConfig(max_output_tokens=800, temperature=0.2),
    )
except Exception as e:
    print(f"\nЗАПИТ ВПАВ: {type(e).__name__}: {e}")
    print("\n  FAILED_PRECONDITION про location — Google ріже вихідний IP.")
    print("    «через проксі: ні»  — увімкни GEMINI_BASE_URL.")
    print("    проксі увімкнений   — воркер ходить не туди.")
    print("  404 від воркера       — не збігається GEMINI_PROXY_SECRET.")
    sys.exit(1)

print(f"\ntext:   {response.text!r}")
print(f"finish: {[getattr(c, 'finish_reason', None) for c in (response.candidates or [])]}")
print(f"block:  {getattr(response, 'prompt_feedback', None)}")
print(f"usage:  {response.usage_metadata}")

if not (response.text or "").strip():
    print("\nВідповідь порожня — дивись finish_reason вище:")
    print("  MAX_TOKENS — модель витратила ліміт (у 2.5 його з'їдає thinking)")
    print("  SAFETY     — спрацював фільтр безпеки")
else:
    print("\nGemini працює. Якщо бот усе одно каже «Халепа» — проблема не в ключі.")
