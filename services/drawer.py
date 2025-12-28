import io
from PIL import Image, ImageDraw, ImageFont

# --- НАЛАШТУВАННЯ СТИЛІВ ---
THEMES = {
    "default": {
        "name": "Default (Cyber)",
        "gradient_start": (15, 12, 41),  # Темно-фіолетовий
        "gradient_end": (48, 43, 99),  # Світліший фіолетовий
        "title_color": (0, 255, 127),  # SpringGreen
        "text_color": (255, 255, 255),  # Білий
        "accent_color": (255, 215, 0),  # Золотий (корона)
        "grade_color": (255, 255, 255),  # Колір головної оцінки
        "line_color": (255, 255, 255, 50)  # Напівпрозора лінія
    },
    "gold": {
        "name": "Luxury Gold",
        "gradient_start": (10, 10, 10),  # Майже чорний
        "gradient_end": (40, 40, 40),  # Темно-сірий
        "title_color": (255, 215, 0),  # Золото
        "text_color": (255, 250, 240),  # Кремовий білий
        "accent_color": (255, 215, 0),  # Золото
        "grade_color": (255, 215, 0),  # Оцінка теж золота
        "line_color": (255, 215, 0, 80)  # Золота лінія
    },
    "matrix": {
        "name": "Hacker",
        "gradient_start": (0, 10, 0),  # Дуже темний зелений
        "gradient_end": (0, 30, 0),  # Темно-зелений
        "title_color": (0, 255, 0),  # Яскравий неон (Lime)
        "text_color": (200, 255, 200),  # Блідо-зелений текст
        "accent_color": (0, 255, 0),  # Неон
        "grade_color": (0, 255, 0),  # Неон
        "line_color": (0, 255, 0, 60)  # Зелена лінія
    },
    "sunset": {
        "name": "Vaporwave Sunset",
        "gradient_start": (45, 0, 50),  # Глибокий пурпур
        "gradient_end": (255, 80, 0),  # Яскравий оранжевий
        "title_color": (255, 255, 0),  # Жовтий
        "text_color": (255, 255, 255),
        "accent_color": (0, 255, 255),  # Ціан (блакитний)
        "grade_color": (255, 255, 255),
        "line_color": (255, 255, 255, 60)
    },
    "ocean": {
        "name": "Deep Ocean",
        "gradient_start": (0, 15, 30),  # Темно-синій
        "gradient_end": (0, 100, 150),  # Морська хвиля
        "title_color": (0, 255, 255),  # Аквамарин
        "text_color": (240, 255, 255),  # Azure
        "accent_color": (255, 255, 0),  # Кораловий (для контрасту)
        "grade_color": (0, 255, 255),
        "line_color": (0, 255, 255, 40)
    }
}


def create_gradient(width, height, start_color, end_color):
    """Створює вертикальний градієнт."""
    base = Image.new('RGB', (width, height), start_color)
    top = Image.new('RGB', (width, height), start_color)
    bottom = Image.new('RGB', (width, height), end_color)
    mask = Image.new('L', (width, height))
    mask_data = []
    for y in range(height):
        mask_data.extend([int(255 * (y / height))] * width)
    mask.putdata(mask_data)
    base.paste(bottom, (0, 0), mask)
    return base


def draw_wrapped(provider: str, username: str, avg_grade: float, lessons_count: int, top_subject: str,
                 is_vip: bool = False, style_name: str = "default"):
    # 1. Вибираємо тему
    theme = THEMES.get(style_name, THEMES["default"])

    # 2. Налаштування полотна
    W, H = 1080, 1920
    img = create_gradient(W, H, theme["gradient_start"], theme["gradient_end"])
    draw = ImageDraw.Draw(img)

    # 3. Шрифти (без змін)
    try:
        font_title = ImageFont.truetype("fonts/Montserrat-Bold.ttf", 80)
        font_big = ImageFont.truetype("fonts/Montserrat-ExtraBold.ttf", 250)
        font_med = ImageFont.truetype("fonts/Montserrat-SemiBold.ttf", 70)
        font_small = ImageFont.truetype("fonts/Montserrat-Regular.ttf", 50)
        font_footer = ImageFont.truetype("fonts/Montserrat-Light.ttf", 40)
    except IOError:
        font_title = ImageFont.load_default()
        font_big = ImageFont.load_default()
        font_med = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_footer = ImageFont.load_default()

    # Хелпери
    def draw_centered_text(y, text, font, color=None):
        if color is None: color = theme["text_color"]  # Колір з теми по дефолту
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (W - text_w) / 2
        draw.text((x, y), text, font=font, fill=color)
        return bbox[3] - bbox[1]

    def draw_centered_text_with_icon(y: int, text: str, font, icon_path: str = None,
                                     icon_size: int = 60, gap: int = 15, color=None):
        if color is None: color = theme["text_color"]

        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]

        if icon_path:
            try:
                icon = Image.open(icon_path).convert("RGBA")
                icon = icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                total_w = icon_size + gap + text_w
                x = (W - total_w) // 2
                icon_y = y + (text_h - icon_size) // 2
                img.paste(icon, (x, icon_y), icon)
                draw.text((x + icon_size + gap, y), text, font=font, fill=color)
            except Exception as e:
                print(f"Icon error: {e}")
                x = (W - text_w) // 2
                draw.text((x, y), text, font=font, fill=color)
        else:
            x = (W - text_w) // 2
            draw.text((x, y), text, font=font, fill=color)

    # --- МАЛЮЄМО ---

    # Заголовок
    draw_centered_text(150, f"{provider.upper()} WRAPPED", font_title, color=theme["title_color"])
    draw_centered_text(250, "Мій тиждень навчання", font_small, color=(200, 200, 200))  # Сірий завжди ок

    # Привітання
    draw_centered_text(400, f"Привіт, {username[:15]}!", font_med, color=theme["text_color"])

    # Оцінка (велика цифра)
    draw_centered_text(600, f"{avg_grade:.1f}", font_big, color=theme["grade_color"])
    draw_centered_text(875, "Середній бал за тиждень", font_small, color=(180, 180, 180))

    # Лінія (колір беремо з теми)
    draw.rectangle([(100, 1000), (980, 1005)], fill=theme["line_color"])

    # Статистика
    draw_centered_text_with_icon(
        y=1100,
        text=f"Оцінок отримано: {lessons_count}",
        font=font_med,
        icon_path="icons/book.png",
        color=theme["text_color"]
    )

    # Найкращий предмет
    draw_centered_text_with_icon(
        y=1300,
        text="Найкращий предмет",
        font=font_small,
        icon_path="icons/crown.png",
        color=theme["accent_color"]  # Використовуємо акцентний колір (золотий/неон/тощо)
    )

    draw_centered_text(1370, top_subject, font_med, color=theme["text_color"])

    # Footer
    footer_text = "Згенеровано у @nzdiary_bot"
    if is_vip:
        footer_text = "NZ Diary Premium"

    draw_centered_text_with_icon(
        y=H - 150,
        text=footer_text,
        font=font_footer,
        icon_path="icons/diamond.png" if is_vip else None,
        icon_size=40,
        color=(150, 150, 150)
    )

    bio = io.BytesIO()
    img.save(bio, format='PNG')
    bio.seek(0)
    return bio
