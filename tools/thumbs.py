#!/usr/bin/env python3
"""Уменьшенные листы выпуска — посмотреть вёрстку и заголовки целиком.

    tools/thumbs.py pn0030634 pn0030633 --out /путь/к/папке

Лист ужимается до 1300×2100: заголовки и рубрики читаются, имена — нет.
Имена в вердикт берутся только из вырезок в исходном разрешении
(`cut.py`, `findword.py`): с уменьшенного листа они выходят выдуманными.
"""

import argparse

import _root
import docstore


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("idents", nargs="+")
    ap.add_argument("--out", help="папка для картинок (иначе $TOOLS_OUT или временная)")
    ap.add_argument("--size", default="1300x2100", metavar="ШxВ")
    a = ap.parse_args()
    box = tuple(int(v) for v in a.size.lower().split("x"))
    out = _root.out_dir(a.out)
    for ident in a.idents:
        total = docstore.load_meta(ident).get("pages") or 0
        for page in range(1, total + 1):
            im = _root.open_scan(ident, page)
            size = im.size
            im.thumbnail(box)
            dst = out / f"t_{ident}_{page}.jpg"
            im.convert("RGB").save(dst, quality=80)
            print(f"{ident} стр. {page}: {size[0]}×{size[1]} → {dst}")


if __name__ == "__main__":
    main()
