#!/usr/bin/env python3
"""Вырезка из скана по долям листа.

    tools/cut.py pn0030634 4 0.19 0.30 0.38 0.36 out.png
    tools/cut.py pn0030634 4 0 0.5 1 1 низ.jpg --max 2000

Координаты — доли ширины и высоты от 0 до 1: x0 y0 x1 y1. Вырезка шире
`--max` точек ужимается; для чтения мелкого списка брать узкий кусок
(колонка шириной около 0.19, высотой около 0.06), чтобы он остался в
исходном разрешении.
"""

import argparse

import _root


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ident")
    ap.add_argument("page", type=int)
    ap.add_argument("box", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("out")
    ap.add_argument("--max", type=int, default=1600, dest="limit",
                    help="наибольшая сторона вырезки, точек")
    a = ap.parse_args()
    x0, y0, x1, y1 = a.box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        ap.error("доли задаются как x0 y0 x1 y1, от 0 до 1, x0 < x1, y0 < y1")
    im = _root.open_scan(a.ident, a.page)
    piece = im.crop((int(x0 * im.width), int(y0 * im.height),
                     int(x1 * im.width), int(y1 * im.height)))
    full = piece.size
    piece.thumbnail((a.limit, a.limit))
    piece.save(a.out)
    note = "" if piece.size == full else f", ужато с {full[0]}×{full[1]}"
    print(f"{a.out}: {piece.size[0]}×{piece.size[1]}{note}")


if __name__ == "__main__":
    main()
