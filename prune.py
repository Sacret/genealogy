#!/usr/bin/env python3
"""Чистка сканов: оставить нужные, остальные выбросить.

Сканы — кэш, а не данные. Любой инструмент дотянет выброшенную страницу
через fetch.ensure_page, поэтому чистить можно смело.

Что остаётся:
  * страницы со всеми когда-либо залогированными кандидатами — не только
    подтверждёнными: отсеявшийся кандидат тоже может понадобиться
    перепроверить;
  * страницы, названные в вердиктах;
  * вырезки в crops/ — они крошечные и служат доказательством находки
    даже если документ уйдёт из библиотеки.

По умолчанию только показывает, что будет сделано. Удаляет с --apply.
"""

import argparse
import re
import shutil
import subprocess
import sys

from docstore import doc_dir, load_meta, read_log, save_meta


def pages_to_keep(ident: str) -> set:
    """Номера страниц, которые нельзя выбрасывать.

    Номера берутся в том числе из текста вердиктов, а там встречаются
    ссылки на другие документы ("тот же человек, что в bv0000386
    стр. 208"). Поэтому набор обрезается по числу страниц этого тома —
    иначе в нём оказываются несуществующие номера.
    """
    keep = set()
    for r in read_log(ident):
        for p in r.get("pages_with_hits", []):
            keep.add(int(p))
        for p in re.findall(r"стр\.?\s*(\d+)", r.get("verdict", ""), re.I):
            keep.add(int(p))
        # 'стр. 280/404/485/537' — номера через косую черту
        for run in re.findall(r"стр\.?\s*([\d/]+)", r.get("verdict", ""), re.I):
            keep.update(int(x) for x in run.split("/") if x)
    total = load_meta(ident).get("pages")
    if total:
        keep = {p for p in keep if 1 <= p <= total}
    return keep


def confirmed_hits(ident: str):
    """(фамилия, страница) для вердиктов со статусом 'найдена'.

    Как и в pages_to_keep, номера обрезаются по объёму тома: вердикт
    может ссылаться на страницу другого документа ("тот же человек, что
    в bv0000386 стр. 208"), и без обрезки мы полезли бы вырезать
    несуществующую страницу.
    """
    total = load_meta(ident).get("pages")
    out, seen = [], set()
    for r in read_log(ident):
        if r.get("type") == "verdict" and r.get("status") == "found":
            for p in re.findall(r"стр\.?\s*(\d+)", r.get("verdict", ""), re.I):
                n = int(p)
                if total and not (1 <= n <= total):
                    continue
                if (r["surname"], n) in seen:
                    continue        # вердикт может называть страницу дважды
                seen.add((r["surname"], n))
                out.append((r["surname"], n))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--apply", action="store_true", help="действительно удалить")
    ap.add_argument("--keep", help="дополнительно сохранить: 12,300-310")
    a = ap.parse_args()

    d = doc_dir(a.ident)
    scans = sorted((d / "scans").glob("p*.jpg"))
    if not scans:
        sys.exit(f"нет сканов в {d / 'scans'}")

    ocr_pages = {f.stem for f in (d / "ocr").glob("p*.txt")}
    missing_ocr = [f for f in scans if f.stem not in ocr_pages]
    if missing_ocr:
        sys.exit(f"СТОП: {len(missing_ocr)} страниц не распознано "
                 f"(например {missing_ocr[0].name}). Сначала ocr_pages.py — "
                 f"иначе чистка потеряет текст безвозвратно.")

    keep = pages_to_keep(a.ident)
    if a.keep:
        from fetch import parse_pages
        keep.update(parse_pages(a.keep))

    doomed = [f for f in scans if int(f.stem[1:]) not in keep]
    freed = sum(f.stat().st_size for f in doomed)
    meta = load_meta(a.ident)

    print(f"{meta.get('title', a.ident)}")
    print(f"  сканов сейчас:  {len(scans)}")
    print(f"  оставить:       {len(scans) - len(doomed)}  "
          f"({', '.join(str(p) for p in sorted(keep)) or '—'})")
    print(f"  удалить:        {len(doomed)}   освободится {freed / 2**20:.0f} МБ")

    if not a.apply:
        print("\nэто предпросмотр. чтобы удалить: prune.py "
              f"{a.ident} --apply")
        return

    # Вырезки делаем ДО удаления: подтверждённая находка должна пережить
    # и чистку, и возможное исчезновение документа из библиотеки.
    for surname, page in confirmed_hits(a.ident):
        if not list((d / "crops").glob(f"p{page:04d}_*")):
            print(f"  вырезаю подтверждение: стр. {page}, {surname}")
            subprocess.run([sys.executable, "crop.py", a.ident, str(page),
                            surname, "--line", "260"], check=False)

    for f in doomed:
        f.unlink()
    save_meta(a.ident, pruned=True, kept_pages=sorted(keep))
    print(f"\nудалено {len(doomed)} сканов, освобождено {freed / 2**20:.0f} МБ")
    print("выброшенные страницы дотянутся сами, когда понадобятся")


if __name__ == "__main__":
    main()
