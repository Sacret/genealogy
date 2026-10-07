"""Картинка для превью ссылок (Open Graph): иконка, название, подзаголовок
и цветная полоса снизу. 1200×630 — размер, который мессенджеры показывают
крупной карточкой.

    python3 og_card.py --icon logo.png --title "Журнал генеалогических поисков" \\
        --subtitle "исторические документы" --accent "#52321f" --out og-image.jpg

Рисует headless Chrome: так текст набирается системными шрифтами с
нормальным кернингом и переносом, а SVG-иконки берутся как есть. Скрипт
не привязан к этому проекту — им же сделана карточка my-mind.

Telegram не дожидается тяжёлых картинок, поэтому итог проверяется по весу:
плоская карточка в PNG весит десятки килобайт, больше 300 КБ — повод
насторожиться.
"""
from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

W, H = 1200, 630
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SANS = '-apple-system, "SF Pro Display", "Helvetica Neue", sans-serif'
TOO_HEAVY = 300_000

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden;
  background: {bg}; color: {ink}; font-family: {font}; }}
.c {{ height: 100%; display: flex; align-items: center; justify-content: center;
  gap: 64px; padding: 0 80px; box-sizing: border-box; }}
img {{ width: {icon_size}px; height: {icon_size}px; flex: none; object-fit: contain; }}
h1 {{ margin: 0; font-size: {title_size}px; font-weight: {weight};
  letter-spacing: {spacing}; line-height: 1.05; }}
p {{ margin: 22px 0 0; font-size: {sub_size}px; color: {muted}; font-weight: 500; }}
.bar {{ position: absolute; left: 0; right: 0; bottom: 0; height: 14px; background: {accent}; }}
</style></head><body>
<div class="c"><img src="{icon}"><div><h1>{title}</h1>{sub}</div></div>
<div class="bar"></div>
</body></html>
"""


def find_chrome() -> str:
    for name in (CHROME, "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name) or (name if Path(name).exists() else None)
        if path:
            return path
    sys.exit("не нашёлся Chrome или Chromium: карточку рисует он")


def render(args: argparse.Namespace) -> Path:
    icon = Path(args.icon).resolve()
    if not icon.exists():
        sys.exit(f"нет иконки {icon}")
    out = Path(args.out).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # иконку кладём рядом со страницей: file:// из headless Chrome
        # не всегда пускает к файлам из других каталогов
        shutil.copy(icon, tmp / icon.name)
        page = tmp / "card.html"
        page.write_text(PAGE.format(
            w=W, h=H, bg=args.bg, ink=args.ink, muted=args.muted,
            accent=args.accent, font=args.font, icon=html.escape(icon.name),
            icon_size=args.icon_size, title_size=args.title_size,
            sub_size=args.sub_size, weight=args.weight,
            spacing=args.letter_spacing, title=html.escape(args.title),
            sub=f"<p>{html.escape(args.subtitle)}</p>" if args.subtitle else "",
        ), encoding="utf-8")
        shot = tmp / "card.png"
        subprocess.run(
            [find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", f"--window-size={W},{H}",
             f"--screenshot={shot}", page.as_uri()],
            check=True, capture_output=True, timeout=60,
        )
        im = Image.open(shot).convert("RGB")
    if im.size != (W, H):
        sys.exit(f"Chrome снял {im.size[0]}×{im.size[1]} вместо {W}×{H}")
    if out.suffix.lower() in (".jpg", ".jpeg"):
        im.save(out, quality=88, optimize=True, progressive=True)
    else:
        im.save(out, optimize=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--icon", required=True, help="PNG или SVG")
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--out", required=True, help=".png или .jpg (у наших превью — .jpg)")
    ap.add_argument("--accent", default="#e5833e", help="цвет полосы снизу")
    ap.add_argument("--bg", default="#fbfaf7")
    ap.add_argument("--ink", default="#1c1b19", help="цвет названия")
    ap.add_argument("--muted", default="#6b6862", help="цвет подзаголовка")
    ap.add_argument("--font", default=SANS, help="CSS font-family")
    ap.add_argument("--weight", default="800", help="насыщенность названия")
    ap.add_argument("--letter-spacing", default="-0.03em")
    ap.add_argument("--icon-size", type=int, default=300)
    ap.add_argument("--title-size", type=int, default=132,
                    help="для длинного названия в две строки — около 84")
    ap.add_argument("--sub-size", type=int, default=50)
    args = ap.parse_args()

    out = render(args)
    size = out.stat().st_size
    print(f"{out}: {W}×{H}, {size // 1024} КБ")
    if size > TOO_HEAVY:
        print("  тяжело для превью: Telegram может не дождаться картинки", file=sys.stderr)


if __name__ == "__main__":
    main()
