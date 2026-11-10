#!/usr/bin/env python3
"""Найти слово на самом скане и вырезать место вокруг него.

    tools/findword.py pn0030634 4 'Кармаз' 'Могуч' --out /путь/к/папке

Читает скан Tesseract с координатами слов (`--psm 4`, колонки) и для
каждого слова, подходящего под выражение, печатает соседние слова и
кладёт вырезку в исходном разрешении. На газете это быстрее `crop.py`
(тот идёт больше двух минут). Координаты слов кэшируются рядом с
вырезками: второй вызов по той же полосе Tesseract не запускает.

Перечни мест на первой полосе и в приказах такое чтение тоже теряет —
их смотрят глазами, по `cut.py`.
"""

import argparse
import csv
import io
import re
import subprocess

import _root
import tess


def words(ident, page, cache, psm):
    tsv = cache / f"tsv_{ident}_{page}_psm{psm}.tsv"
    if not tsv.exists():
        r = tess.check(subprocess.run(
            ["tesseract", str(_root.scan(ident, page)), "-", "-l", "rus",
             "--psm", psm, "tsv"],
            capture_output=True, timeout=tess.TIMEOUT), f"tesseract {ident} стр. {page}")
        tsv.write_bytes(r.stdout)
    rows = csv.DictReader(io.StringIO(tsv.read_text(encoding="utf-8",
                                                    errors="replace")),
                          delimiter="\t", quoting=csv.QUOTE_NONE)
    return [r for r in rows if (r.get("text") or "").strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ident")
    ap.add_argument("page", type=int)
    ap.add_argument("patterns", nargs="+", metavar="REGEX")
    ap.add_argument("--out", help="папка для вырезок (иначе $TOOLS_OUT или временная)")
    ap.add_argument("--psm", default="4")
    ap.add_argument("--pad", type=int, nargs=2, default=(900, 160),
                    metavar=("ВШИРЬ", "ВВЫСЬ"), help="поля вокруг слова, точек")
    a = ap.parse_args()
    out = _root.out_dir(a.out)
    rows = words(a.ident, a.page, out, a.psm)
    im = _root.open_scan(a.ident, a.page)
    found = 0
    for i, row in enumerate(rows):
        if not any(re.search(p, row["text"], re.I) for p in a.patterns):
            continue
        x, y, w, h = (int(row[c]) for c in ("left", "top", "width", "height"))
        box = (max(0, x - a.pad[0]), max(0, y - a.pad[1]),
               min(im.width, x + w + a.pad[0]), min(im.height, y + h + a.pad[1]))
        dst = out / f"fw_{a.ident}_{a.page}_{found}.png"
        im.crop(box).save(dst)
        context = " ".join(r["text"] for r in rows[max(0, i - 6):i + 7])
        print(f"{found}  {row['text']}  ({x}, {y})  | {context}\n   {dst}")
        found += 1
    if not found:
        print("совпадений нет — это не значит, что слова нет на полосе")


if __name__ == "__main__":
    main()
