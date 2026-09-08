#!/usr/bin/env python3
"""Вырезание найденных слов из скана — для проверки глазами.

Текстовый поиск даёт только смещение в строке; чтобы увидеть слово на
странице, нужны его координаты. Tesseract умеет отдавать их в TSV, но
разбивает текст на слова иначе, чем это делает find.py, поэтому здесь
идёт независимый проход: берём боксы слов и прогоняем через тот же
матчер.

    python3 crop.py bv0000407 494 Могучевъ

Если по целой странице слово не нашлось, идёт второй проход — полосами,
теми же, что режет rescue.py. Иначе находка, которую вытащило только
перечитывание полосами, осталась бы без вырезки: так вышло с фельдшером
Могучевым на стр. 176 тома bv0000031, где основное распознавание съело
фамилию целиком, оставив один адрес.

Третий проход — по колонкам. Полоса газеты, снятая ниже 45, не даётся ни
целиком, ни лентами: лента пересекает все столбцы разом и склеивает
соседние строки. Вырезанная колонка читается как единый блок и отдаёт
имена, которых не видели первые два прохода. Так нашёлся урядник Яков
Кармазин в списке присяжных заседателей Донецкого округа на стр. 3
выпуска pn0024160 (39,7 средней по выпуску): страничный поиск не дал по
нему ни одного кандидата даже при расширенном пороге.
"""

import argparse, csv, io, json, pathlib, subprocess, sys, tempfile

from docstore import allow_big_scans, doc_dir
from fetch import ensure_page
from rescue import BAND, STEP
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


def overlap(a, b) -> float:
    """Доля пересечения двух боксов от меньшего из них."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (x1 - x0) * (y1 - y0) / small if small else 0.0


def words_in_bands(img, lang="rus", psm="6"):
    """То же, но по горизонтальным полосам внахлёст, с пересчётом в
    координаты страницы.

    Полосы те же, что у rescue.py: узкая полоса читается лучше целой
    страницы — соседние строки не мешают, и Tesseract не пытается
    разложить два столбца в один поток.

    Полосы идут внахлёст, поэтому одно и то же слово приходит дважды, и
    повторы отсеиваются по пересечению боксов, а не по тексту: соседние
    полосы читают слово чуть по-разному («Могучевъ» и «Могучевь»), и
    отсев по тексту пропускал обе вырезки в журнал.
    """
    from PIL import Image
    im = Image.open(img)
    w, h = im.size
    seen = []
    with tempfile.TemporaryDirectory() as tmp:
        band = pathlib.Path(tmp) / "band.jpg"
        for top in range(0, h - 60, STEP):
            im.crop((0, top, w, min(h, top + BAND))).save(band, dpi=(400, 400))
            for text, (x0, y0, x1, y1) in words(band, lang, psm):
                box = (x0, y0 + top, x1, y1 + top)
                if any(overlap(box, b) > 0.5 for b in seen):
                    continue
                seen.append(box)
                yield text, box


def columns(img, top=200, foot=40):
    """Границы колонок полосы — по провалам плотности чёрного.

    Вертикальных линеек между столбцами газета не печатает, а межколонник
    узок, поэтому провал ищется не до нуля: порог берётся долей от
    распределения, и в границы попадает середина каждого провала. Шапка
    и подвал отрезаются — они идут во всю ширину и провалы заплывают.
    """
    import numpy as np
    from PIL import Image

    im = Image.open(img) if not hasattr(img, "size") else img
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
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 200]


def words_in_columns(img, lang="rus", psm="6"):
    """То же, что words_in_bands, но по колонкам, с пересчётом координат."""
    from PIL import Image

    im = Image.open(img)
    with tempfile.TemporaryDirectory() as tmp:
        col = pathlib.Path(tmp) / "col.jpg"
        for left, right in columns(im):
            im.crop((left, 200, right, im.height - 40)).save(col, dpi=(400, 400))
            for text, (x0, y0, x1, y1) in words(col, lang, psm):
                yield text, (x0 + left, y0 + 200, x1 + left, y1 + 200)


def matches(stem, text, thr, fragile) -> bool:
    """Слово из TSV, похожее на искомую фамилию не хуже порога."""
    norm = normalize(text)
    return bool(norm) and prefix_distance(stem, norm, fragile=fragile)[0] <= thr


def main():
    allow_big_scans()
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("page", type=int)
    ap.add_argument("surname")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--pad", type=int, default=25, help="поля вокруг слова, px")
    ap.add_argument("--line", type=int, default=0,
                    help="если >0, вырезать всю строку такой высоты вокруг слова")
    ap.add_argument("--psm", help="режим разбора страницы; по умолчанию тот, "
                                  "которым документ распознавали")
    a = ap.parse_args()

    from PIL import Image

    d = doc_dir(a.ident)
    img = ensure_page(a.ident, a.page)   # дотянет, если скан был вычищен

    # Читать вырезку надо тем же режимом, каким читали страницу: у газеты
    # это `--psm 4`, и с книжным `6` TSV-проход сваливает колонки в кашу,
    # а слово, которое поиск видел, здесь не находится вовсе. Режим
    # записан в quality.json тем же прогоном, что и уверенность.
    psm = a.psm
    if not psm:
        qf = d / "quality.json"
        psm = (json.loads(qf.read_text(encoding="utf-8")).get("psm", "6")
               if qf.exists() else "6")

    stem, fragile = stem_query(a.surname)
    thr = a.threshold if a.threshold is not None else default_threshold(stem)

    out = d / "crops"
    out.mkdir(exist_ok=True)
    im = Image.open(img)
    found = 0
    seen = list(words(img, psm=psm))
    if not any(matches(stem, t, thr, fragile) for t, _ in seen):
        seen = list(words_in_bands(img))     # страница не далась — читаем полосами
    if not any(matches(stem, t, thr, fragile) for t, _ in seen):
        seen = list(words_in_columns(img))   # и полосы не дались — по колонкам
    for text, (x0, y0, x1, y1) in seen:
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
        print(f"на стр. {a.page} совпадений не нашлось ни по целой странице, "
              f"ни полосами (TSV-проход режет слова иначе, чем построчный)")


if __name__ == "__main__":
    main()
