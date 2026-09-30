#!/usr/bin/env python3
"""Сплошной проход газетных страниц вертикальными лентами.

Колонки из `rescue.py --cols` берут лишь часть листа, а страницы не ниже
порога штатно не перечитываются вовсе. Здесь каждая страница режется во
всю высоту на ленты шириной в пятую часть листа с шагом в десятую, и
каждая лента читается отдельно (`--psm 6`). Результат пишется в
`<id>/ocr_stripes/`; find.py в этот слой сам не заглядывает, его читают
глазами при разборе кандидатов (выписка слов на «Кар»/«Мог»).

    python3 stripes.py pn0026083
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--lang", default="rus")
    a = ap.parse_args()
    root = Path(a.ident)
    out = root / "ocr_stripes"
    out.mkdir(exist_ok=True)
    for scan in sorted((root / "scans").glob("p*.jpg")):
        im = Image.open(scan).convert("L")
        w, h = im.size
        width, step = w // 5, w // 10
        parts, x = [], 0
        while True:
            crop = im.crop((x, 0, min(x + width, w), h))
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            crop.save(tmp)
            parts.append(subprocess.run(
                ["tesseract", tmp, "-", "-l", a.lang, "--psm", "6"],
                capture_output=True, text=True).stdout)
            os.remove(tmp)
            if x + width >= w:
                break
            x += step
        (out / (scan.stem + ".txt")).write_text("\n".join(parts))
        print(scan.stem, flush=True)


if __name__ == "__main__":
    main()
