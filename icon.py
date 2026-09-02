#!/usr/bin/env python3
"""Иконка журнала: ять на тёмном поле.

Ять — эмблема дореформенной орфографии, то есть ровно того материала,
с которым работает система. Форма узнаваема даже в 16 px, если взять
жирную антикву и не мельчить.

Каждый размер рисуется отдельно, а не уменьшается из большого: серифная
буква при даунсэмплинге в 16 px превращается в кашу.
"""

import pathlib

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
SIZES = (16, 24, 32, 48, 64, 128, 256)

INK = (82, 50, 31)         # тёмная охра: контраст с бумагой держится в 16 px,
                           # тогда как светлый --accent журнала там мутнеет
PAPER = (250, 248, 245)    # --bg журнала


def render(size: int, ink=INK, paper=PAPER) -> Image.Image:
    # Рисуем вчетверо крупнее и уменьшаем: края скругления и штрихи
    # выходят гладкими, а сама буква всё равно набирается под свой кегль.
    ss = 4
    n = size * ss
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    radius = int(n * (0.16 if size >= 32 else 0.09))
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=radius, fill=ink + (255,))

    # Кегль под поле: в мелких размерах буква должна занимать больше места,
    # иначе теряется совсем.
    # В мелких размерах поля крадут почти всю букву, поэтому кегль растёт.
    frac = 0.88 if size <= 16 else (0.82 if size <= 24 else 0.72)
    f = ImageFont.truetype(FONT, int(n * frac))
    x0, y0, x1, y1 = d.textbbox((0, 0), "ѣ", font=f)
    d.text(((n - (x1 - x0)) / 2 - x0, (n - (y1 - y0)) / 2 - y0),
           "ѣ", font=f, fill=paper + (255,))
    return im.resize((size, size), Image.LANCZOS)


# В 16 px засечки и тонкая перекладина ятя слипаются при любом кегле,
# поэтому самый мелкий размер набирается не шрифтом, а по пикселям:
# стойка, перекладина у верха и замкнутая чаша справа внизу.
_YAT_16 = [
    "................",
    "................",
    ".....##.........",
    "..########......",
    "..########......",
    ".....##.........",
    ".....##.........",
    ".....########...",
    ".....##.....##..",
    ".....##......##.",
    ".....##......##.",
    ".....##......##.",
    ".....##.....##..",
    ".....########...",
    "................",
    "................",
]


def render_pixel(size=16, ink=INK, paper=PAPER) -> Image.Image:
    im = Image.new("RGBA", (size, size), ink + (255,))
    px = im.load()
    for y, row in enumerate(_YAT_16):
        for x, c in enumerate(row):
            if c == "#":
                px[x, y] = paper + (255,)
    # углы срезаем по одному пикселю — рамка перестаёт выглядеть наклейкой
    for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
        px[x, y] = (0, 0, 0, 0)
    return im


def _ico_bytes(sizes) -> bytes:
    import io
    imgs = [render_pixel(s) if s == 16 else render(s) for s in sizes]
    buf = io.BytesIO()
    imgs[-1].save(buf, format="ICO", sizes=[(s, s) for s in sizes],
                  append_images=imgs[:-1])
    return buf.getvalue()


def build(path="favicon.ico"):
    pathlib.Path(path).write_bytes(_ico_bytes(SIZES))
    return pathlib.Path(path)


def data_uri() -> str:
    """Компактная иконка прямо в HTML.

    Ссылка на внешний favicon.ico сломалась бы, стоит открыть или
    переслать journal.html отдельно от папки. Для встраивания берём
    только ходовые размеры — полный набор утроил бы вес страницы.
    """
    import base64
    b = _ico_bytes((16, 32, 48))
    return "data:image/x-icon;base64," + base64.b64encode(b).decode()


if __name__ == "__main__":
    p = build()
    print(f"{p}  {p.stat().st_size} байт")
    # лист для просмотра: реальные размеры и увеличенные
    sheet = Image.new("RGB", (760, 300), (238, 234, 228))
    x = 20
    for s in SIZES:
        im = render(s)
        sheet.paste(im, (x, 30), im)
        big = im.resize((96, 96), Image.NEAREST)
        sheet.paste(big, (x, 150), big)
        x += max(s, 96) + 18
    sheet.save("icon_preview.png")
    print("icon_preview.png")
