#!/usr/bin/env python3
"""Весь конвейер по документу одной командой.

    python3 run.py https://vivaldi.dspl.ru/bv0000387
    python3 run.py https://vivaldi.dspl.ru/bv0000387 --find Кармазинъ Могучевъ

Шаги идут по порядку и каждый возобновляем, так что повторный запуск
дочитывает недостающее, а не начинает заново.
"""

import argparse
import subprocess
import sys
import time

import catalog
from docstore import doc_id, load_meta


def step(title, cmd):
    print(f"\n=== {title} ===", flush=True)
    t = time.monotonic()
    r = subprocess.run([sys.executable, *cmd])
    if r.returncode:
        sys.exit(f"шаг «{title}» упал (код {r.returncode})")
    print(f"    {time.monotonic() - t:.0f} c", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="URL документа во вьюере Vivaldi")
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--find", nargs="*", default=[], help="фамилии для поиска")
    ap.add_argument("--skip-rescue", action="store_true",
                    help="без перечитывания ненадёжных страниц полосами")
    a = ap.parse_args()

    ident = doc_id(a.base)
    step("скачивание", ["fetch.py", a.base, "--dpi", str(a.dpi),
                        "--delay", str(a.delay)])
    step("распознавание", ["ocr_pages.py", ident, "--lang", "rus", "--psm", "6"])
    # Полутоновые сканы (тонкая бумага, просвечивает оборот) даёт не всякий
    # том, но проверка дешёвая и сама пропускает уже бинарные.
    step("бинаризация", ["prep.py", ident])
    step("замер надёжности", ["quality.py", ident])
    if not a.skip_rescue:
        # Дорогой шаг: только страницы, которые quality.py признал плохими.
        step("перечитывание полосами", ["rescue.py", ident])

    for surname in a.find:
        step(f"поиск: {surname}", ["find.py", ident, surname])

    # Без --find поиска не было, а значит, и никто не пересобрал список:
    # том уже скачан и распознан, и в очереди ему больше не место.
    catalog.refresh(ident)
    print(f"\nготово: {load_meta(ident).get('title', ident)}")


if __name__ == "__main__":
    main()
