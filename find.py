#!/usr/bin/env python3
"""Поиск фамилии в распознанном документе.

    python3 find.py bv0000407 Кармазинъ
    python3 find.py bv0000407 Кармазинъ --loose      # шире сеть, больше мусора
    python3 find.py bv0000407 --history              # что уже искали
"""

import argparse
import json
import re
import sys

import journal
from docstore import add_verdict, doc_dir, load_meta, log_search, print_log
from surnamefind.search import find_in_pages, stem_query
from surnamefind.match import default_threshold


def _split_first_word(text: str):
    m = re.match(r"\s*([^\s]+)(.*)", text, re.S)
    return (m.group(1), m.group(2)) if m else ("", text)


def load_pages(ident: str):
    """Страницы по порядку, со склейкой переносов на их границе.

    В книге слово регулярно рвётся между страницами ('...Буда-' / 'ринъ...').
    Без склейки фамилия отсутствует в тексте как токен, и никакой поиск её
    не найдёт. Хвост переносим на страницу, где перенос начался — там же,
    где его потом проверять по скану.
    """
    files = sorted((doc_dir(ident) / "ocr").glob("p*.txt"))
    if not files:
        sys.exit(f"нет распознанного текста для {ident} — сначала ocr_pages.py")
    # Дополнительные прочтения тех же страниц: полосами (rescue.py) и по
    # бинаризованному скану (prep.py). Ищем по всем сразу — то, что
    # развалилось в одном прочтении, часто цело в другом.
    extra = [doc_dir(ident) / "ocr_bands", doc_dir(ident) / "ocr_prep"]
    pages = []
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        for sub in extra:
            alt = sub / f.name
            if alt.exists():
                t += "\n" + alt.read_text(encoding="utf-8", errors="replace")
        pages.append([f.name, t])

    for i in range(len(pages) - 1):
        tail = pages[i][1].rstrip()
        if not tail.endswith(("-", "‐", "‑", "–")):
            continue
        head, rest = _split_first_word(pages[i + 1][1].lstrip())
        pages[i][1] = tail[:-1] + head
        pages[i + 1][1] = rest
    return [(n, t) for n, t in pages]


def page_no(name: str) -> str:
    return name.replace(".txt", "").lstrip("p").lstrip("0") or "0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident", help="идентификатор документа, напр. bv0000407")
    ap.add_argument("surname", nargs="?")
    ap.add_argument("--history", action="store_true", help="журнал поисков по документу")
    ap.add_argument("--loose", action="store_true", help="+1.0 к порогу")
    ap.add_argument("--strict", action="store_true", help="только точное совпадение основы")
    ap.add_argument("--short", action="store_true",
                    help="ловить и обломки в 2-3 буквы перед переносом ('Мо-гучевъ'); "
                         "для доказательства отсутствия, а не для обычного поиска")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-log", action="store_true", help="не записывать в журнал")
    ap.add_argument("--verdict", help="записать итог проверки глазами и выйти")
    ap.add_argument("--status", choices=("found", "absent", "unclear"),
                    default="unclear", help="итог: найдена / нет / неясно")
    ap.add_argument("--pages", help="страницы, где фамилия подтверждена глазами, "
                                    "через запятую: '223'. Только эти журнал "
                                    "выделяет и снабжает вырезкой")
    a = ap.parse_args()

    if a.verdict and a.surname:
        pages = re.findall(r"\d+", a.pages) if a.pages else None
        add_verdict(a.ident, a.surname, a.verdict, a.status, pages)
        print(f"журнал: {journal.rebuild()}")
        print_log(a.ident)
        return

    if a.history or not a.surname:
        print_log(a.ident)
        return

    stem, _ = stem_query(a.surname)
    thr = a.threshold
    if thr is None:
        thr = default_threshold(stem)
        if a.loose:
            thr += 1.0
        if a.strict:
            thr = 0.0

    hits = find_in_pages(load_pages(a.ident), a.surname, threshold=thr,
                         min_fragment=2 if a.short else 4)
    if not a.no_log:
        log_search(a.ident, a.surname, stem, thr, hits)
        journal.rebuild()

    if a.json:
        print(json.dumps([h.__dict__ for h in hits], ensure_ascii=False, indent=2))
        return

    meta = load_meta(a.ident)
    print(f"{meta.get('title', a.ident)} — {meta.get('pages', '?')} стр.")
    print(f"основа: {stem!r}   порог: {thr}   найдено: {len(hits)}\n")
    for h in hits:
        flag = "½ " if h.partial else ("  " if h.cost == 0 else "~ ")
        print(f"{flag}стр. {page_no(h.page):>4}   {h.raw!r}  (score {h.score})")
        print(f"       …{h.context}…")
        print(f"       скан: {doc_dir(a.ident)}/scans/{h.page.replace('.txt', '.jpg')}\n")
    if any(h.cost for h in hits):
        print("~ = совпадение с искажениями, проверьте по скану глазами")
    if any(h.partial for h in hits):
        print("½ = совпало только начало: слово разорвано переносом, "
              "продолжение не распозналось. Смотрите скан — такие чаще всего настоящие.")


if __name__ == "__main__":
    main()
