#!/usr/bin/env python3
"""Повторное распознавание ненадёжных страниц полосами.

На странице целиком Tesseract сваливает узкие колонки таблицы в кашу:
'Могучевъ' на стр. 208 документа bv0000386 стал 'Л/огу-' + '4:65'.
Если резать страницу на горизонтальные полосы внахлёст и распознавать
каждую отдельно, соседние строки перестают влиять друг на друга, и та
же фамилия читается как 'Могу-' — обломка уже достаточно, чтобы поиск
по половинкам её нашёл.

Дорого (в разы медленнее обычного прохода), поэтому применяется только
к страницам, которые quality.py признал ненадёжными.
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

from docstore import allow_big_scans, doc_dir, load_meta

BAND, STEP = 190, 95        # высота полосы и шаг: внахлёст, чтобы строка
                            # не разрезалась пополам ни при каком смещении


def bands(args):
    from PIL import Image
    img, dst, tmp = args
    if dst.exists() and dst.stat().st_size > 0:
        return True
    im = Image.open(img)
    w, h = im.size
    out = []
    for y in range(0, h - 60, STEP):
        im.crop((0, y, w, min(h, y + BAND))).save(tmp, dpi=(400, 400))
        r = subprocess.run(["tesseract", str(tmp), "-", "-l", "rus", "--psm", "6"],
                           capture_output=True)
        # decode вручную: на битой полосе tesseract изредка отдаёт не-UTF8,
        # и падение одной страницы не должно ронять весь прогон
        out.append(r.stdout.decode("utf-8", "replace"))
    dst.write_text("\n".join(out), encoding="utf-8")
    return False


def main():
    allow_big_scans()
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--pages", help="явный список вместо ненадёжных: 208,210-212")
    a = ap.parse_args()

    d = doc_dir(a.ident)
    qf = d / "quality.json"
    if a.pages:
        from fetch import parse_pages
        pages = parse_pages(a.pages)
    elif qf.exists():
        pages = json.loads(qf.read_text(encoding="utf-8"))["weak"]
    else:
        sys.exit(f"нет {qf} — сначала quality.py {a.ident}")

    out = d / "ocr_bands"
    out.mkdir(exist_ok=True)
    # Свой каталог на процесс, а не общий `_work`: два прогона по одному
    # документу (например, длинный фоновый и короткий по одной странице)
    # чистили каталог друг другу, и длинный падал на полдороге с
    # FileNotFoundError на своей же полосе.
    work = pathlib.Path(tempfile.mkdtemp(prefix="bands-", dir=d))

    jobs = []
    for p in pages:
        img = d / "scans" / f"p{p:04d}.jpg"
        if not img.exists():
            from fetch import ensure_page
            img = ensure_page(a.ident, p)
        # Временный файл — свой на страницу, а не на номер потока: пул не
        # гарантирует, что задача i попадёт к работнику i, и два потока
        # затирали друг другу полосу, портя JPEG.
        jobs.append((img, out / f"p{p:04d}.txt", work / f"band_p{p:04d}.jpg"))

    print(f"{load_meta(a.ident).get('title', a.ident)}: полосами {len(jobs)} стр.")
    fresh = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, cached in enumerate(ex.map(bands, jobs), 1):
            fresh += not cached
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)
    shutil.rmtree(work, ignore_errors=True)
    print(f"готово: {len(jobs)} стр., заново {fresh}")


if __name__ == "__main__":
    main()
