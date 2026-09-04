#!/usr/bin/env python3
"""Чистка сканов: оставить нужные, остальные выбросить.

Сканы — кэш, а не данные. Любой инструмент дотянет выброшенную страницу
через fetch.ensure_page, поэтому чистить можно смело.

Что остаётся:
  * страницы со всеми когда-либо залогированными кандидатами — не только
    подтверждёнными: отсеявшийся кандидат тоже может понадобиться
    перепроверить;
  * страницы находок, названные вердиктом (`find.py --pages`);
  * вырезки в crops/ — они крошечные и служат доказательством находки
    даже если документ уйдёт из библиотеки.

По умолчанию только показывает, что будет сделано. Удаляет с --apply.
"""

import argparse
import re
import shutil
import subprocess
import sys

from docstore import (ROOT, doc_dir, documents, latest_verdicts, load_meta,
                      read_log, save_meta)


# Ссылка на другой документ: "тот же человек, что в bv0000386 стр. 208".
# Такой номер принадлежит соседнему тому, но существует и в этом, так что
# обрезкой по объёму его не отсечь — нужно смотреть, что стоит перед ним.
CROSS_REF = re.compile(r"bv\d{7}[^.;]{0,60}?стр\.?\s*[\d/]+", re.I)
PAGE_REF = re.compile(r"стр\.?\s*([\d/]+)", re.I)


def _numbers(text: str, total=None) -> list:
    """Номера страниц этого тома, названные в тексте вердикта."""
    out = []
    for run in PAGE_REF.findall(CROSS_REF.sub(" ", text)):
        out += [int(x) for x in run.split("/") if x]   # 'стр. 280/404/485'
    if total:
        out = [p for p in out if 1 <= p <= total]
    return out


def confirmed_pages(rec: dict, total=None) -> list:
    """Страницы, где фамилия действительно стоит.

    Из поля `confirmed`, куда их кладёт `find.py --pages`. Для вердиктов,
    записанных до появления поля, остаётся разбор текста — он и был
    причиной ошибки: в тексте называются и отклонённые кандидаты, и
    находки в соседних томах.
    """
    if rec.get("confirmed") is not None:
        pages = [int(p) for p in rec["confirmed"]]
        return [p for p in pages if not total or 1 <= p <= total]
    if rec.get("status") == "found":
        return _numbers(rec.get("verdict", ""), total)
    return []


def pages_to_keep(ident: str) -> set:
    """Номера страниц, которые нельзя выбрасывать."""
    total = load_meta(ident).get("pages")
    keep = set()
    for r in read_log(ident):
        for p in r.get("pages_with_hits", []):
            keep.add(int(p))
    # Только последние вердикты: перепроверка дописывает строку, и
    # прежняя не должна удерживать страницу, которую новая уже отвела.
    for r in latest_verdicts(ident).values():
        keep.update(confirmed_pages(r, total))
        # Кроме находки держим и всё, что вердикт называет по имени:
        # отклонённого кандидата могут захотеть пересмотреть, а страница,
        # проверенная руками, в pages_with_hits не попадает вовсе.
        keep.update(_numbers(r.get("verdict", ""), total))
    return keep


def confirmed_hits(ident: str):
    """(фамилия, страница) для вердиктов со статусом 'найдена'."""
    total = load_meta(ident).get("pages")
    out, seen = [], set()
    for r in latest_verdicts(ident).values():
        if r.get("status") == "found":
            for n in confirmed_pages(r, total):
                if (r["surname"], n) in seen:
                    continue        # вердикт может называть страницу дважды
                seen.add((r["surname"], n))
                out.append((r["surname"], n))
    return out


# --- список находок в .gitignore --------------------------------------
#
# Сканы выброшены целиком (`*/scans/*`), кроме страниц с подтверждёнными
# находками: на них стоит весь результат работы, а восстановление кэша
# держится на том, что документ остаётся доступен в библиотеке.
#
# Список этих исключений вёлся руками — и разошёлся с журналом: из
# шестнадцати страниц находок в репозиторий попали десять, а строки
# дописывались в случайные места файла, в том числе после раздела о
# порождаемых файлах. Теперь его пишет `sync_gitignore` по вердиктам.

GITIGNORE = ROOT / ".gitignore"
MARK_BEGIN = "# --- страницы находок: список ведёт prune.py ---"
MARK_END = "# --- конец списка находок ---"
KEEP_LINE = re.compile(r"^!(bv\d+/scans/p\d+\.jpg)\s*$")
ANCHOR = "*/scans/*"


def finding_pages() -> dict:
    """Страница находки → подпись по умолчанию, по всем документам."""
    out = {}
    for ident in documents():
        meta = load_meta(ident)
        total = meta.get("pages")
        year = re.search(r"\b(1[6-9]\d\d)\b", meta.get("title", ""))
        for surname, rec in sorted(latest_verdicts(ident).items()):
            if rec.get("status") != "found":
                continue
            kin = {str(p) for p in (rec.get("kin") or [])}
            for page in confirmed_pages(rec, total):
                path = f"{ident}/scans/p{page:04d}.jpg"
                note = f"{year.group(1) + ', ' if year else ''}{surname}"
                if str(page) in kin:
                    note += ", родство подтверждено"
                out.setdefault(path, note)
    return dict(sorted(out.items()))


def sync_gitignore() -> list:
    """Переписать список находок в .gitignore. Возвращает добавленное.

    Подписи, написанные руками, сохраняются: в них есть номер приказа,
    станица и чин, которых из вердикта не вытащить. Своя подпись даётся
    только новой странице.
    """
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines()

    # Старые записи могут лежать где угодно — собираем их отовсюду
    # вместе с подписью, стоящей строкой выше, и вырезаем из файла.
    notes, rest = {}, []
    for line in lines:
        m = KEEP_LINE.match(line)
        if m:
            if rest and rest[-1].startswith("#"):
                notes[m.group(1)] = rest.pop().lstrip("# ").strip()
            continue
        if line in (MARK_BEGIN, MARK_END):
            continue
        rest.append(line)

    pages = finding_pages()
    block = [MARK_BEGIN]
    for path, default in pages.items():
        block.append(f"# {notes.get(path, default)}")
        block.append(f"!{path}")
    block.append(MARK_END)

    if ANCHOR in rest:
        at = rest.index(ANCHOR) + 1
    else:                                   # якоря нет — кладём в конец
        at = len(rest)
        block = ["", *block]
    out = [*rest[:at], "", *block, *rest[at:]]

    # Пустые строки схлопываем: блок вырезали вместе с окружением, и
    # повторный прогон иначе растил бы файл вниз на одну строку за раз.
    tidy = [ln for i, ln in enumerate(out)
            if ln.strip() or (i and out[i - 1].strip())]
    GITIGNORE.write_text("\n".join(tidy).rstrip("\n") + "\n", encoding="utf-8")
    return [p for p in pages if p not in notes]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident", nargs="?")
    ap.add_argument("--apply", action="store_true", help="действительно удалить")
    ap.add_argument("--keep", help="дополнительно сохранить: 12,300-310")
    ap.add_argument("--gitignore", action="store_true",
                    help="переписать список страниц находок в .gitignore")
    a = ap.parse_args()

    if a.gitignore:
        added = sync_gitignore()
        print(f"{GITIGNORE.name}: страниц находок {len(finding_pages())}"
              + (f", новых {len(added)}" if added else ""))
        for path in added:
            print("  +", path)
        return
    if not a.ident:
        ap.error("нужен идентификатор документа (или --gitignore)")

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
