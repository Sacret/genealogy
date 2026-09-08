#!/usr/bin/env python3
"""Повторное распознавание ненадёжных страниц — полосами и колонками.

На странице целиком Tesseract сваливает узкие колонки таблицы в кашу:
'Могучевъ' на стр. 208 документа bv0000386 стал 'Л/огу-' + '4:65'.
Если резать страницу на горизонтальные полосы внахлёст и распознавать
каждую отдельно, соседние строки перестают влиять друг на друга, и та
же фамилия читается как 'Могу-' — обломка уже достаточно, чтобы поиск
по половинкам её нашёл.

Полосы, однако, не чинят газету: лента идёт во всю ширину листа и
пересекает все столбцы разом. Для этого есть второй режим, `--cols`:
полоса режется по вертикали, и каждая колонка читается отдельным блоком
(`--psm 6`) — столбец без соседей распознаётся заметно лучше, чем он же
в составе страницы. Так на стр. 3 выпуска pn0024160 (38,7 средней по
выпуску, худший в подшивке) нашёлся урядник Яков Кармазин: страничный
поиск не дал по нему ни одного кандидата даже при расширенном пороге.

Полосы пишутся в `ocr_bands/`, колонки в `ocr_cols/`; find.py ищет по
обоим наравне с основным прочтением.

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


def columns(im, top=200, foot=40, minw=200):
    """Границы колонок полосы — по провалам плотности чёрного.

    Вертикальных линеек между столбцами газета не печатает, а межколонник
    узок, поэтому провал ищется не до нуля: порог берётся долей от
    распределения, и в границы попадает середина каждого провала. Шапка и
    подвал отрезаются — они идут во всю ширину и провалы там заплывают.
    """
    import numpy as np

    a = np.array(im.convert("L").crop((0, top, im.width, im.height - foot)))
    dark = (a < 150).sum(axis=0)
    smooth = np.convolve(dark, np.ones(31) / 31, mode="same")
    floor = np.percentile(smooth[smooth > 0], 20)
    dips, inside = [], False
    for x, v in enumerate(smooth):
        if v < floor and not inside:
            start, inside = x, True
        elif v >= floor and inside:
            if x - start > 10:
                dips.append((start + x) // 2)
            inside = False
    edges = [0] + dips + [im.width]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a > minw]


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


def cols(args):
    """То же, что bands, но по колонкам: каждая — отдельным блоком."""
    from PIL import Image
    img, dst, tmp = args
    if dst.exists() and dst.stat().st_size > 0:
        return True
    im = Image.open(img)
    out = []
    for left, right in columns(im):
        im.crop((left, 200, right, im.height - 40)).save(tmp, dpi=(400, 400))
        r = subprocess.run(["tesseract", str(tmp), "-", "-l", "rus", "--psm", "6"],
                           capture_output=True)
        out.append(r.stdout.decode("utf-8", "replace"))
    dst.write_text("\n".join(out), encoding="utf-8")
    return False


def main():
    allow_big_scans()
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--pages", help="явный список вместо ненадёжных: 208,210-212")
    ap.add_argument("--cols", action="store_true",
                    help="резать не полосами, а колонками — для газеты")
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

    mode, out = (cols, d / "ocr_cols") if a.cols else (bands, d / "ocr_bands")
    out.mkdir(exist_ok=True)
    # Свой каталог на процесс, а не общий `_work`: два прогона по одному
    # документу (например, длинный фоновый и короткий по одной странице)
    # чистили каталог друг другу, и длинный падал на полдороге с
    # FileNotFoundError на своей же полосе.
    work = pathlib.Path(tempfile.mkdtemp(prefix=mode.__name__ + "-", dir=d))

    jobs = []
    for p in pages:
        img = d / "scans" / f"p{p:04d}.jpg"
        if not img.exists():
            from fetch import ensure_page
            img = ensure_page(a.ident, p)
        # Временный файл — свой на страницу, а не на номер потока: пул не
        # гарантирует, что задача i попадёт к работнику i, и два потока
        # затирали друг другу полосу, портя JPEG.
        jobs.append((img, out / f"p{p:04d}.txt", work / f"part_p{p:04d}.jpg"))

    how = "колонками" if a.cols else "полосами"
    print(f"{load_meta(a.ident).get('title', a.ident)}: {how} {len(jobs)} стр.")
    fresh = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, cached in enumerate(ex.map(mode, jobs), 1):
            fresh += not cached
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)
    shutil.rmtree(work, ignore_errors=True)
    print(f"готово: {len(jobs)} стр., заново {fresh}")


if __name__ == "__main__":
    main()
