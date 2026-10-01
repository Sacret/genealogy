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
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import tess
from docstore import doc_dir, load_meta
from ocr_pages import load_stats

THRESHOLD = 60.0          # ниже — странице верить нельзя


def page_stats(args):
    """(страница, уверенность, слов); сбой Tesseract — исключение.

    Раньше упавший процесс давал пустой вывод и страницу с нулевой
    уверенностью: её молча объявляли слабой, а quality.json ложился на
    диск с неверным замером.
    """
    img, psm = args
    _, conf, words = tess.recognize(img, "rus", psm)
    return int(img.stem[1:]), conf, words


def measure(scans, psm, folder, jobs, remeasure=False):
    """Замер по всем сканам: готовое из ocr_stats.json, остальное — Tesseract.

    `ocr_pages.py` уже получил уверенность тем же проходом, каким читал
    текст, тем же режимом (psm), так что повторное распознавание тома
    ради одной колонки TSV — чистая трата. Статистику чужого режима
    `load_stats` не отдаёт, и тогда замер идёт как прежде.
    """
    known = {} if remeasure else load_stats(folder, "rus", psm)
    stats, todo = [], []
    for f in scans:
        got = known.get(str(int(f.stem[1:])))
        if got:
            stats.append((int(f.stem[1:]), got["conf"], got["words"]))
        else:
            todo.append(f)
    if todo:
        with ThreadPoolExecutor(jobs) as ex:
            futures = [(f, ex.submit(page_stats, (f, psm))) for f in todo]
            failed = {}
            for f, fut in futures:
                try:
                    stats.append(fut.result())
                except Exception as e:
                    failed[int(f.stem[1:])] = f"{type(e).__name__}: {e}"
        if failed:
            sys.exit("замер не удался на стр. "
                     + ", ".join(map(str, sorted(failed)))
                     + f"\n  quality.json не тронут; первая ошибка: "
                       f"{failed[min(failed)]}")
    return sorted(stats), len(scans) - len(todo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--psm", default="6",
                    help="тот же режим, каким страницу распознавали: "
                         "у газет 4, у книг 6")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--remeasure", action="store_true",
                    help="не брать уверенность из ocr_stats.json, а "
                         "распознать страницы заново")
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

    stats, reused = measure(scans, a.psm, d, a.jobs, a.remeasure)

    weak = [(p, c) for p, c, n in stats if c < a.threshold]
    confs = [c for _, c, _ in stats]
    print(f"{load_meta(a.ident).get('title', a.ident)}")
    print(f"  страниц измерено: {len(stats)} "
          f"(готовых из ocr_stats.json: {reused})")
    print(f"  средняя уверенность: {sum(confs)/len(confs):.1f}")
    print(f"  ниже порога {a.threshold}: {len(weak)} стр. "
          f"({len(weak)/len(stats):.0%})")
    if weak:
        print("  им нельзя верить на отрицательный ответ:")
        print("   ", ", ".join(str(p) for p, _ in weak[:40]),
              "…" if len(weak) > 40 else "")

    (d / "quality.json").write_text(json.dumps(
        {"threshold": a.threshold, "psm": a.psm,
         "pages": {str(p): round(c, 1) for p, c, _ in stats},
         "weak": [p for p, _ in weak]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"  записано: {d/'quality.json'}")


if __name__ == "__main__":
    main()
