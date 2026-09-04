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

import catalog
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


OLD_LETTERS = "ѢѣІіѲѳѴѵ"


def bare_old_spelling(text: str):
    """Дореформенные формы, оставленные в прозе вердикта голыми.

    Вердикт читает современный человек, и старое написание должно
    отмечать ровно то, что взято из оттиска. Имя, набранное в прозе как
    'Алексѣй Могучевъ', ничего не отмечает — оно просто выглядит опечаткой
    журнала; поэтому имена людей пишутся современно, а форма из документа
    берётся в кавычки («Могучевъ Иванъ, переп. 1904 года») или в апострофы,
    если это распознанное слово ('Рогачевъ').

    Правило приходилось напоминать трижды (тома за 1894, 1909 и 1873 годы),
    так что проверять его глазами оказалось недостаточно надёжно.

    Конечный `ъ` считается дореформенным, внутренний — нет: «объявленъ»
    нарушает правило, «объявлен» и «разъяснение» — нет. Родительный на
    -аго («Донскаго») ловится тем же перебором.
    """
    # Кавычки и апострофы гасим, сохраняя длину: внутри них старое
    # написание законно, и туда проверка не смотрит.
    outside = re.sub(r"«[^»]*»|'[^']*'|\"[^\"]*\"",
                     lambda m: " " * len(m.group()), text)
    bad = [w for w in re.findall(r"[^\W\d_]+", outside) if old_form(w)]
    return sorted(set(bad), key=str.lower)


# Единственные современные слова, кончающиеся на -аго: на них правило
# окончаний спотыкается, и проще назвать их поимённо.
NOT_OLD = {"благо", "чикаго", "сантьяго"}


def old_form(word: str) -> bool:
    if re.search(f"[{OLD_LETTERS}]", word):
        return True
    if word[-1] in "ъЪ":                       # конечный еръ: «урядникъ»
        return True
    low = word.lower()                          # родительный на -аго: «Донскаго»
    return len(low) > 4 and low.endswith(("аго", "яго")) and low not in NOT_OLD


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
                    help="итог: найдена / нет / неясно. Обязателен при --verdict")
    ap.add_argument("--pages", help="страницы, где фамилия подтверждена глазами, "
                                    "через запятую: '223'. Только эти журнал "
                                    "выделяет и снабжает вырезкой")
    ap.add_argument("--kin", help="страницы из --pages, где найден человек с "
                                  "установленным родством, а не однофамилец. "
                                  "Их журнал красит зелёным, остальные находки "
                                  "— синим «родство не установлено»")
    a = ap.parse_args()

    if a.verdict and a.surname:
        # Итог требуется назвать словом. Раньше --status по умолчанию был
        # 'unclear', и вердикт, начинающийся с «НЕ НАЙДЕНА», ложился в
        # журнал как «не проверено»: текст читает человек, а разбирает
        # строки — журнал (так и вышло с томом за 1877 год).
        if not a.status:
            sys.exit("вердикт без --status: скажите found / absent / unclear")
        stale = bare_old_spelling(a.verdict)
        if stale:
            sys.exit("дореформенное написание вне кавычек: " + ", ".join(stale)
                     + "\nимена людей в прозе пишутся современно (Алексей "
                       "Могучев), формы из оттиска берутся в кавычки «…» "
                       "или в апострофы '…'")
        pages = re.findall(r"\d+", a.pages) if a.pages else None
        if a.status == "found" and not pages:
            sys.exit("--status found без --pages: назовите страницы находки, "
                     "иначе журнал возьмёт номера из текста вердикта")
        kin = re.findall(r"\d+", a.kin) if a.kin else None
        # Однофамилец, покрашенный как родственник, — ошибка молчаливая:
        # в журнале он выглядит доказанным. Поэтому родство называется
        # только страницами, уже подтверждёнными глазами.
        if kin and not set(kin) <= set(pages or []):
            sys.exit("--kin называет страницы, которых нет в --pages: "
                     + ", ".join(sorted(set(kin) - set(pages or []))))
        add_verdict(a.ident, a.surname, a.verdict, a.status, pages, kin)
        print(f"журнал: {journal.rebuild()}")
        # Вердикт — это и есть момент, когда том уходит из очереди в
        # просмотренные. Список, который правят руками, назавтра врёт.
        print(f"каталог: {catalog.refresh(a.ident)}")
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
        catalog.refresh(a.ident)

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
