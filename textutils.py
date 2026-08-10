"""Нарізка довгих повідомлень під ліміти Telegram.

Без залежностей — тестується ізольовано.
"""

TELEGRAM_LIMIT = 4096   # ліміт тексту повідомлення
CAPTION_LIMIT = 1024    # ліміт підпису до фото


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Ріже текст на частини не довші за limit.

    Ріжемо по межах абзаців («\\n\\n»), а не по символах: HTML-теги в цьому
    проєкті завжди закриваються в межах абзацу, тому розмітка не рветься.
    Якщо один абзац сам довший за ліміт — ріжемо його по рядках.
    """
    if not text:
        return [text]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue

        flush()

        if len(block) <= limit:
            current = block
            continue

        # абзац-переросток: збираємо по рядках
        for line in block.splitlines(keepends=True):
            if len(current) + len(line) > limit:
                flush()
                # рядок довший за ліміт — рубаємо жорстко
                while len(line) > limit:
                    chunks.append(line[:limit])
                    line = line[limit:]
            current += line

    flush()
    return chunks
