#!/usr/bin/env python3
"""Оценка надёжности OCR постранично.

Нужна из-за конкретного провала: фамилия 'Могучевъ' на стр. 208
документа bv0000386 набрана курсивом в узкой колонке таблицы, и
Tesseract прочёл её как 'Л/огу-' + '4:65'. Никакой поиск по такому
тексту её не найдёт, а отрицательный результат выглядел бы
достоверным. Отсюда правило: отрицательный ответ имеет силу только
там, где распознавание надёжно, и страницы низкого доверия должны
быть названы поимённо.

Мера — средняя уверенность Tesseract по словам (колонка conf в TSV).
Таблицы с курсивом дают 45-55, обычный текст 65-80.
"""

import argparse
import csv
import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from docstore import doc_dir, load_meta

THRESHOLD = 60.0          # ниже — странице верить нельзя


def page_stats(img):
    out = subprocess.run(["tesseract", str(img), "-", "-l", "rus", "--psm", "6", "tsv"],
                         capture_output=True, text=True).stdout
    rows = [r for r in csv.DictReader(io.StringIO(out), delimiter="\t",
                                      quoting=csv.QUOTE_NONE)
            if (r.get("text") or "").strip()]
    confs = [float(r["conf"]) for r in rows if float(r["conf"]) >= 0]
    if not confs:
        return int(img.stem[1:]), 0.0, 0
    return int(img.stem[1:]), sum(confs) / len(confs), len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--refetch", action="store_true",
                    help="дотянуть вычищенные сканы, чтобы измерить том целиком")
    a = ap.parse_args()

    d = doc_dir(a.ident)
    meta = load_meta(a.ident)
    scans = sorted((d / "scans").glob("p*.jpg"))
    total = meta.get("pages", 0)

    # Замер по неполному набору сканов дал бы неверную картину и — что
    # опаснее — затёр бы верный quality.json. После чистки сканов замер
    # возможен только с --refetch, который тянет том обратно.
    if total and len(scans) < total:
        if not a.refetch:
            sys.exit(
                f"{a.ident}: на диске {len(scans)} сканов из {total} — "
                f"похоже, том чистили (prune.py).\n"
                f"  Замер по неполному набору перезапишет quality.json "
                f"неверными данными.\n"
                f"  Дотянуть и измерить: quality.py {a.ident} --refetch")
        from fetch import ensure_page
        print(f"дотягиваю недостающие страницы: {total - len(scans)} шт.")
        for n in range(1, total + 1):
            ensure_page(a.ident, n)
        scans = sorted((d / "scans").glob("p*.jpg"))
    if not scans:
        sys.exit(f"нет сканов в {d/'scans'} — сначала fetch.py")

    with ThreadPoolExecutor(a.jobs) as ex:
        stats = sorted(ex.map(page_stats, scans))

    weak = [(p, c) for p, c, n in stats if c < a.threshold]
    confs = [c for _, c, _ in stats]
    print(f"{load_meta(a.ident).get('title', a.ident)}")
    print(f"  страниц измерено: {len(stats)}")
    print(f"  средняя уверенность: {sum(confs)/len(confs):.1f}")
    print(f"  ниже порога {a.threshold}: {len(weak)} стр. "
          f"({len(weak)/len(stats):.0%})")
    if weak:
        print("  им нельзя верить на отрицательный ответ:")
        print("   ", ", ".join(str(p) for p, _ in weak[:40]),
              "…" if len(weak) > 40 else "")

    (d / "quality.json").write_text(json.dumps(
        {"threshold": a.threshold,
         "pages": {str(p): round(c, 1) for p, c, _ in stats},
         "weak": [p for p, _ in weak]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"  записано: {d/'quality.json'}")


if __name__ == "__main__":
    main()
