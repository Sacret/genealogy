#!/usr/bin/env python3
"""Вырезание найденных слов из скана — для проверки глазами.

Текстовый поиск даёт только смещение в строке; чтобы увидеть слово на
странице, нужны его координаты. Tesseract умеет отдавать их в TSV, но
разбивает текст на слова иначе, чем это делает find.py, поэтому здесь
идёт независимый проход: берём боксы слов и прогоняем через тот же
матчер.

    python3 crop.py bv0000407 494 Могучевъ
"""

import argparse, csv, io, pathlib, subprocess, sys

from docstore import doc_dir
from fetch import ensure_page
from surnamefind.normalize import normalize
from surnamefind.search import stem_query
from surnamefind.match import prefix_distance, default_threshold, score


def words(img: pathlib.Path, lang="rus", psm="6"):
    """Слова страницы с их прямоугольниками, из TSV-выдачи Tesseract."""
    out = subprocess.run(
        ["tesseract", str(img), "-", "-l", lang, "--psm", psm, "tsv"],
        capture_output=True, text=True).stdout
    rows = csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        yield text, (int(r["left"]), int(r["top"]),
                     int(r["left"]) + int(r["width"]),
                     int(r["top"]) + int(r["height"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("page", type=int)
    ap.add_argument("surname")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--pad", type=int, default=25, help="поля вокруг слова, px")
    ap.add_argument("--line", type=int, default=0,
                    help="если >0, вырезать всю строку такой высоты вокруг слова")
    a = ap.parse_args()

    from PIL import Image

    d = doc_dir(a.ident)
    img = ensure_page(a.ident, a.page)   # дотянет, если скан был вычищен

    stem, fragile = stem_query(a.surname)
    thr = a.threshold if a.threshold is not None else default_threshold(stem)

    out = d / "crops"
    out.mkdir(exist_ok=True)
    im = Image.open(img)
    found = 0
    for text, (x0, y0, x1, y1) in words(img):
        norm = normalize(text)
        if not norm:
            continue
        cost, _ = prefix_distance(stem, norm, fragile=fragile)
        if cost > thr:
            continue
        found += 1
        if a.line:
            box = (0, max(0, y0 - a.line // 2), im.width,
                   min(im.height, y1 + a.line // 2))
        else:
            box = (max(0, x0 - a.pad), max(0, y0 - a.pad),
                   min(im.width, x1 + a.pad), min(im.height, y1 + a.pad))
        dst = out / f"p{a.page:04d}_{stem}_{found}.png"
        im.crop(box).save(dst)
        print(f"{dst}   {text!r}  score {score(stem, norm, fragile=fragile):.3f}")
    if not found:
        print(f"на стр. {a.page} совпадений не нашлось "
              f"(TSV-проход режет слова иначе, чем построчный)")


if __name__ == "__main__":
    main()
