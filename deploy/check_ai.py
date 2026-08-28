"""Діагностика Gemini: запускати на сервері, коли бот відповідає «Халепа».

    cd /opt/nz/app && sudo -u nzbot /opt/nz/venv/bin/python deploy/check_ai.py

Друкує реальну помилку замість того, щоб ковтати її, як це робить ai().
Ключ не виводиться — тільки факт наявності й довжина.
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GEMINI_API_KEY") or ""
model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

print(f"GEMINI_API_KEY: {'є' if key else 'НЕМАЄ'} (довжина {len(key)})")
print(f"GEMINI_MODEL:   {model}")
print(f"cwd:            {os.getcwd()}")

if not key:
    print("\n.env не містить GEMINI_API_KEY або лежить не в робочій теці.")
    sys.exit(1)

try:
    from google import genai
    from google.genai import types
except ImportError as e:
    print(f"\nПакет google-genai не встановлений у venv: {e}")
    sys.exit(1)

client = genai.Client(api_key=key)

try:
    response = client.models.generate_content(
        model=model,
        contents=["Скажи одним словом: працює?"],
        config=types.GenerateContentConfig(max_output_tokens=800, temperature=0.2),
    )
except Exception as e:
    print(f"\nЗАПИТ ВПАВ: {type(e).__name__}: {e}")
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
