#!/usr/bin/env python3
"""Бинаризация сканов и третий проход распознавания.

Тома в библиотеке отсканированы по-разному. В bv0000386/387/388/389 сканы
уже жёстко бинаризованы (84-92% пикселей — чистые 0 или 255). А в bv0000390
(1880 г.) это серые полутоновые снимки тонкой бумаги, сквозь которую
просвечивает оборот: поверх текста лежит зеркальный текст с обратной
стороны, и Tesseract читает оба разом. Средняя уверенность по тому — 52
против 62-65 у остальных.

Просвет светлее чернил, поэтому порог его снимает начисто. Порог берётся
от гистограммы самой страницы (перцентиль), а не фиксированный: яркость
плавает от разворота к развороту.

Результат кладётся отдельным слоем ocr_prep/, а не поверх ocr/: разные
прочтения одной страницы дополняют друг друга, и find.py ищет по всем.
"""

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from docstore import allow_big_scans, doc_dir, load_meta

PERCENTILE = 5.0          # подобрано по отдаче фамилий на стр. 392 bv0000390
BINARY_SHARE = 0.20       # выше этой доли чистых 0/255 скан уже бинарный


def is_binary(path) -> bool:
    """Скан уже чёрно-белый? Тогда обрабатывать нечего."""
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(path).convert("L"))
    h = np.bincount(a.ravel(), minlength=256)
    return (h[0] + h[255]) / a.size > BINARY_SHARE


def binarize(src, dst, percentile=PERCENTILE):
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(src).convert("L")).astype(np.float32)
    t = np.percentile(a, percentile)
    Image.fromarray((a > t).astype(np.uint8) * 255).save(dst, dpi=(400, 400))


def job(args):
    img, dst, tmp, pct = args
    if dst.exists() and dst.stat().st_size > 0:
        return "готово"
    if is_binary(img):
        return "уже бинарный"
    binarize(img, tmp, pct)
    r = subprocess.run(["tesseract", str(tmp), "-", "-l", "rus", "--psm", "6"],
                       capture_output=True)
    dst.write_text(r.stdout.decode("utf-8", "replace"), encoding="utf-8")
    return "обработан"


def main():
    allow_big_scans()
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--percentile", type=float, default=PERCENTILE)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--pages", help="только эти страницы: 392,400-410")
    a = ap.parse_args()

    d = doc_dir(a.ident)
    if a.pages:
        from fetch import parse_pages
        want = parse_pages(a.pages)
        scans = [d / "scans" / f"p{n:04d}.jpg" for n in want]
        from fetch import ensure_page
        scans = [ensure_page(a.ident, n) for n in want]
    else:
        scans = sorted((d / "scans").glob("p*.jpg"))
    if not scans:
        sys.exit(f"нет сканов в {d/'scans'}")

    out = d / "ocr_prep"; out.mkdir(exist_ok=True)
    work = d / "_prep"; work.mkdir(exist_ok=True)
    jobs = [(s, out / (s.stem + ".txt"), work / f"{s.stem}.png", a.percentile)
            for s in scans]

    print(f"{load_meta(a.ident).get('title', a.ident)}: {len(jobs)} стр.")
    tally = {}
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, res in enumerate(ex.map(job, jobs), 1):
            tally[res] = tally.get(res, 0) + 1
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    print("итог: " + ", ".join(f"{k} — {v}" for k, v in sorted(tally.items())))


if __name__ == "__main__":
    main()
