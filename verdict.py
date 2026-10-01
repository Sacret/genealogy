#!/usr/bin/env python3
"""Черновик вердикта «не найдена» по распознанному тексту.

    python3 verdict.py draft Кармазинъ pn0026136 pn0026137
    python3 verdict.py accept Кармазинъ pn0026136 pn0026137
    python3 verdict.py accept Кармазинъ pn0026136 --note "стр. 3: «картии» — обрывок слова"
    python3 verdict.py accept Кармазинъ pn0026136 pn0026137 \\
        --note pn0026137="стр. 4 — «магазинт»: объявление"

Почти все вердикты в журнале — отрицательные и почти слово в слово
одинаковые: места не найдены, кандидаты — обычные слова, такие-то полосы
ниже порога надёжности. Руками их пишут по каждому выпуску и каждой
фамилии, и числа в них («две полосы, 52,6 и 50,9») вписаны от руки:
через месяц никто не сверит их с `quality.json`.

`draft` прогоняет обычный, расширенный и «обломочный» поиск, сам отсеивает
слова из `dismissed_words.json` и показывает остальное для разбора глазами.
`accept` записывает вердикт `absent` по каждому документу — вместе с
`evidence`: слабыми страницами, прочитанными слоями и отсеянными словами.
Проза строится из тех же чисел, и `audit.py` может проверить их по данным.

Автоматически не решается ничего, что похоже на находку: точное
совпадение и слово, разорванное переносом, всегда уходят человеку, а
вердикт `found` пишется только вручную через `find.py --verdict`.
"""

import argparse
import json
import re
import sys

import catalog
import find
import journal
import prune
from docstore import ROOT, add_verdict, doc_dir, load_meta, log_search
from surnamefind.match import default_threshold, prefix_distance
from surnamefind.normalize import normalize
from surnamefind.search import find_in_pages, stem_query

DISMISSED = ROOT / "dismissed_words.json"

# Места, которые ищутся вместе с фамилией: название для прозы и слово
# для поиска. Название волости и округа само по себе — прилагательное,
# поэтому ищется оно.
DEFAULT_PLACES = (("Ровеньки", "Ровеньки"),
                  ("Ровенецкая волость", "Ровенецкая"),
                  ("Кочетовская", "Кочетовская"),
                  ("Миусский округ", "Миусский"))

LAYER_NAMES = {
    "ocr": "основное распознавание",
    "ocr_bands": "горизонтальные ленты",
    "ocr_cols": "колонки",
    "ocr_prep": "бинаризованный скан",
}
# Порог для мест. Замер на 38 выпусках «Казачьего вестника» 1885 г.: до 1,0
# включительно — искажённый «Миусский» («Мусскаго», «Мпусскаго», «Мтусскомъ»,
# около 40 мест против 5 точных), с 1,3 — уже шум («Мирскому», «Минскь»,
# «Мускулы»). Полный порог фамилии (1,4–2,0) завалил бы разбор шумом.
PLACE_THRESHOLD = 1.0
# Не больше стольких слабых страниц называем поимённо в прозе: у плохой
# книги их сотни, и список тонет в тексте, а полный лежит в quality.json.
WEAK_LISTED = 15


def plural(n, one, few, many):
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def num(x):
    """Число с десятичной запятой: 52,6, а не 52.6."""
    return f"{x:.1f}".replace(".", ",")


def load_dismissed(path=None):
    """Слова, которые заведомо не фамилия: основа → {слово: причина}.

    Список привязан к основе, а не общий: «могучий» — обычное слово для
    поиска «Могучевъ», но для запроса «Могучий» его отсеивать нельзя.
    """
    path = path or DISMISSED
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8")).get("по_основе", {})
    return {stem: {normalize(w): reason for w, reason in words.items()}
            for stem, words in data.items()}


def continuation_fits(hit, stem, fragile, limit):
    """Годится ли продолжение обрывка до фамилии: True / False / None.

    Обрывок перед переносом ('Мо-') — улов поиска с короткими обломками,
    и в газете он почти весь мусор: 'Лл-', 'ББ-'. Человек отсеивает его
    одним взглядом на следующее слово, и то же делает эта проверка: обрыв
    склеивается со словом после него и сравнивается с основой тем же
    расстоянием, что и в поиске. Склейка не проходит — обрывок отсеян.

    None — судить не по чему: после обрыва нет читаемого слова (конец
    страницы, цифры, знаки). Такой обрывок остаётся человеку, потому что
    'Могу-' + '4:65' и есть тот самый случай bv0000386.
    """
    at = hit.context.find(hit.raw)
    if at < 0:
        return None
    nxt = re.match(r"\s*[|]?\s*([^\W\d_]+)", hit.context[at + len(hit.raw):])
    if not nxt:
        return None
    joined = normalize(hit.raw.rstrip("-‐‑–") + nxt.group(1))
    return prefix_distance(stem, joined, fragile=fragile)[0] <= limit


# Настоящие фамилии, в которые опечатка набора превращает искомую. Обычно
# это историк Карамзин, но в bv0000032 на стр. 547 книги (скан 370) стоит
# «Карамзинъ Ф. П., Азовскій баз.» — Кармазин, набранный с перестановкой
# букв, и его отсеяли как историка. Такое слово с инициалами рядом —
# человек из списка, а не цитата, и решает его только человек.
LOOKALIKES = {"кармазин": ("карамзин",)}
INITIAL = r"[А-ЯЁІѢ][а-яёіѣ]{0,4}\s*[.,’']"
INITIALS_AFTER = re.compile(r"^\W{0,3}\s*" + INITIAL)
INITIALS_BEFORE = re.compile(INITIAL + r"\s*$")


def initials_near(hit):
    """Стоят ли инициалы или сокращённое имя сразу до или после слова."""
    context = getattr(hit, "context", "") or ""
    at = context.find(hit.raw)
    if at < 0:
        return False
    return bool(INITIALS_AFTER.match(context[at + len(hit.raw):])
                or INITIALS_BEFORE.search(context[:at]))


def classify(hit, dismissed, fits=None, stem=None):
    """(класс, причина): exact | partial | fragment | dismissed | review.

    Отсев применяется только к искажённым совпадениям целого слова и к
    обрывкам, чьё продолжение явно не фамилия (`fits is False`). Точное
    (cost 0) и обрыв без вывода остаются человеку, даже если слово
    случайно значится в списке: ошибка здесь стоила бы пропущенного предка.
    """
    if hit.partial:
        if fits is False:
            return "fragment", "обрывок слова, продолжение не похоже на фамилию"
        return "partial", None
    if hit.cost == 0:
        return "exact", None
    word = normalize(hit.raw)
    if (any(word.startswith(w) for w in LOOKALIKES.get(stem, ()))
            and initials_near(hit)):
        return "initials", "похожая фамилия с инициалами — возможна опечатка набора"
    reason = dismissed.get(word)
    if reason:
        return "dismissed", reason
    return "review", None


def page_number(hit) -> int:
    return int(find.page_no(hit.page))


def load_quality(ident):
    path = doc_dir(ident) / "quality.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_stripes(ident):
    """Маркер сплошного прохода лентами (stripes.json) или None.

    Сам слой в репозиторий не идёт и find.py его не ищет, поэтому в
    оговорку он попадает только по целому маркеру — тому же, по которому
    audit.py проверяет вердикты, ссылающиеся на сплошной проход.
    """
    path = doc_dir(ident) / "stripes.json"
    if not path.exists():
        return None
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    total = load_meta(ident).get("pages")
    if marker.get("pages") != total or marker.get("of") != total:
        return None
    return marker


def layer_counts(ident):
    """Сколько страниц прочитано в каждом слое, который ищет find.py."""
    out = {}
    for name in ("ocr", *find.EXTRA_LAYERS):
        out[name] = len(list((doc_dir(ident) / name).glob("p*.txt")))
    return {k: v for k, v in out.items() if v}


def gather(ident, surname, places=DEFAULT_PLACES, dismissed=None, pages=None):
    """Всё, что нужно для вердикта: кандидаты, места, слои, слабые страницы."""
    meta = load_meta(ident)
    pages = pages if pages is not None else find.load_pages(ident)
    dismissed = load_dismissed() if dismissed is None else dismissed
    stem, _ = stem_query(surname)
    base = default_threshold(stem)

    # Три прогона — те, что при ручной проверке делают по очереди:
    # обычный порог, расширенный и обломки в 2–3 буквы перед переносом.
    runs = (("normal", base, 4), ("loose", base + 1.0, 4), ("short", base, 2))
    counts, seen, normal_hits = {}, {}, []
    for name, threshold, fragment in runs:
        hits = find_in_pages(pages, surname, threshold=threshold,
                             min_fragment=fragment)
        counts[name] = len(hits)
        if name == "normal":
            normal_hits = hits
        for h in hits:
            seen.setdefault((h.page, h.start, h.raw), h)

    fragile = stem_query(surname)[1]
    candidates = []
    for h in sorted(seen.values(), key=lambda h: (page_number(h), h.start)):
        fits = (continuation_fits(h, stem, fragile, base + 1.0)
                if h.partial else None)
        kind, reason = classify(h, dismissed.get(stem, {}), fits, stem)
        candidates.append({"page": page_number(h), "raw": h.raw, "kind": kind,
                           "reason": reason, "cost": h.cost,
                           "context": h.context})

    # Место — слово, а не обломок: 'Рово-' перед переносом совпало бы с
    # «Ровеньки» в любой газете. Обрывок учитывается, только если его
    # продолжение не опровергает место. Искажения OCR учитываются так же,
    # как у фамилии, но с порогом PLACE_THRESHOLD: точный поиск пропускал
    # «Мусскаго» и «Мпусскаго», и вердикт писал «мест не найдено».
    place_hits, place_words = {}, {}
    for label, query in places:
        pstem, pfragile = stem_query(query)
        found = [h for h in find_in_pages(pages, query,
                                          threshold=PLACE_THRESHOLD)
                 if not h.partial
                 or continuation_fits(h, pstem, pfragile,
                                      PLACE_THRESHOLD) is not False]
        if found:
            place_hits[label] = sorted({page_number(h) for h in found})
            place_words[label] = sorted({h.raw for h in found})

    quality = load_quality(ident)
    weak = {}
    if quality:
        scores = quality.get("pages", {})
        weak = {str(p): scores.get(str(p)) for p in quality.get("weak", [])}
    return {
        "ident": ident, "surname": surname, "title": meta.get("title", ident),
        "pages_total": meta.get("pages"), "stem": stem, "threshold": base,
        "counts": counts, "normal_hits": normal_hits, "candidates": candidates,
        "places": [label for label, _ in places], "place_hits": place_hits,
        "place_words": place_words,
        "layers": layer_counts(ident), "quality": quality, "weak": weak,
        "stripes": load_stripes(ident),
    }


def unresolved(ctx):
    return [c for c in ctx["candidates"]
            if c["kind"] not in ("dismissed", "fragment")]


def problems(ctx, note=None):
    """Почему вердикт нельзя записать автоматически; пусто — можно."""
    out = []
    ident = ctx["ident"]
    if ctx["quality"] is None:
        out.append(f"{ident}: нет quality.json — сначала quality.py, иначе "
                   "оговорку о полноте не из чего строить")
    total = ctx["pages_total"]
    if not total or ctx["layers"].get("ocr") != total:
        out.append(f"{ident}: основное распознавание неполно "
                   f"({ctx['layers'].get('ocr', 0)} из {total})")
    risky = [c for c in ctx["candidates"] if c["kind"] in ("exact", "partial", "initials")]
    if risky:
        out.append(f"{ident}: точные или оборванные совпадения "
                   f"({', '.join(sorted({c['raw'] for c in risky}))}) — "
                   "решает человек, вердикт пишется через find.py --verdict")
    pending = unresolved(ctx) or ctx["place_hits"]
    if pending and not (note or "").strip():
        out.append(f"{ident}: есть неразобранное (кандидаты или места) — "
                   "разберите глазами и опишите в --note")
    if (note or "").strip() and not pending:
        out.append(f"{ident}: разбирать нечего, а --note дан — заметка "
                   "относится к другому документу?")
    # Та же проверка, что в record(): здесь она стоит до записи первого
    # документа, иначе пачка оборвалась бы на середине.
    stale = find.bare_old_spelling(build_text(ctx, note))
    if stale:
        out.append(f"{ident}: дореформенное написание вне кавычек: "
                   + ", ".join(stale) + " — возьмите в «» или '' в --note")
    return out


def parse_notes(specs, idents):
    """{документ: заметка} из --note; ошибки — sys.exit.

    Разбор у каждого выпуска свой, поэтому при нескольких документах
    заметка привязывается к документу: `--note pn0026136="стр. 4 — …"`.
    Общая заметка вписалась бы в вердикт соседа, где такого кандидата нет.
    """
    notes = {}
    for spec in specs or []:
        ident, sep, text = spec.partition("=")
        if sep and ident.strip() in idents:
            key, text = ident.strip(), text
        elif len(idents) == 1:
            key, text = idents[0], spec
        else:
            sys.exit(f"--note {spec[:40]!r}: при нескольких документах "
                     "назовите документ: --note ДОКУМЕНТ=\"текст\"")
        if key in notes:
            sys.exit(f"--note для {key} дан дважды")
        notes[key] = text
    return notes


def pages_phrase(pages):
    return "стр. " + ", ".join(str(p) for p in sorted(set(pages)))


def coverage_text(ctx):
    total, quality = ctx["pages_total"], ctx["quality"]
    layers = ctx["layers"]
    read = ", ".join(f"{LAYER_NAMES.get(k, k)} ({layers[k]} из {total})"
                     for k in ("ocr", *find.EXTRA_LAYERS) if k in layers)
    text = f"ОГОВОРКА О ПОЛНОТЕ: слои чтения — {read}; "
    if ctx.get("stripes"):
        text += (f"сплошной проход вертикальными лентами ({total} из {total}, "
                 "stripes.json) поиском не читается — только выпиской "
                 "stripes.py --words; ")
    weak = ctx["weak"]
    threshold = quality["threshold"] if quality else "?"
    if not weak:
        return text + f"страниц ниже порога {threshold:g} нет."
    n = len(weak)
    ordered = sorted(weak, key=int)
    shown = ", ".join(f"стр. {p} — {num(weak[p])}" for p in ordered[:WEAK_LISTED])
    more = (f" и ещё {n - WEAK_LISTED} (полный список в quality.json)"
            if n > WEAK_LISTED else "")
    return (text + f"ниже порога {threshold:g}: "
            f"{n} {plural(n, 'страница', 'страницы', 'страниц')} — {shown}{more}. "
            "Отрицательный ответ по ним слабее, чем по остальным.")


def build_text(ctx, note=None):
    """Проза вердикта. Всё, что в ней названо числом, взято из ctx."""
    total = ctx["pages_total"]
    parts = [f"«{ctx['title']}», {total} "
             f"{plural(total, 'страница', 'страницы', 'страниц')}."]

    if ctx["place_hits"]:
        found = "; ".join(f"{label} — {pages_phrase(pages)}"
                          for label, pages in ctx["place_hits"].items())
        parts.append(f"Места встречаются: {found}.")
    else:
        parts.append("Места найдены не были: " + ", ".join(ctx["places"])
                     + " в распознанном тексте не встречаются (с учётом "
                     f"искажений распознавания до {num(PLACE_THRESHOLD)}).")

    c = ctx["counts"]
    parts.append(f"Поиск «{ctx['surname']}»: кандидатов при обычном пороге "
                 f"{c['normal']}, при расширенном {c['loose']}, с обломками "
                 f"слов {c['short']}.")
    by_word = {}
    for cand in ctx["candidates"]:
        if cand["kind"] == "dismissed":
            by_word.setdefault((normalize(cand["raw"]), cand["reason"]),
                               []).append(cand)
    if by_word:
        items = []
        for (_, reason), cands in sorted(by_word.items(),
                                         key=lambda kv: kv[1][0]["page"]):
            words = ", ".join(sorted({f"«{x['raw']}»" for x in cands}))
            items.append(f"{words} ({reason}; "
                         f"{pages_phrase(x['page'] for x in cands)})")
        parts.append("Отсеяно автоматически по списку обычных слов: "
                     + "; ".join(items) + ".")
    fragments = [x for x in ctx["candidates"] if x["kind"] == "fragment"]
    if fragments:
        words = ", ".join(sorted({f"«{x['raw']}»" for x in fragments}))
        parts.append("Обрывки слов перед переносом, чьё продолжение не похоже "
                     f"на фамилию: {words} "
                     f"({pages_phrase(x['page'] for x in fragments)}).")
    if (note or "").strip():
        parts.append("Разбор остальных кандидатов: " + note.strip())
    elif not ctx["candidates"]:
        parts.append("Кандидатов нет.")
    elif not unresolved(ctx):
        parts.append("Все кандидаты — обычные слова.")
    parts.append(coverage_text(ctx))
    return "\n\n".join(parts)


def evidence(ctx, note=None):
    """Те же числа в виде полей: по ним audit.py сверит прозу с данными."""
    return {
        "source": "verdict.py",
        "pages_total": ctx["pages_total"],
        "threshold": ctx["quality"]["threshold"],
        "weak_pages": ctx["weak"],
        "layers": ctx["layers"],
        "stripes": bool(ctx.get("stripes")),
        "candidates": ctx["counts"],
        "dismissed": [{"page": c["page"], "word": c["raw"]}
                      for c in ctx["candidates"] if c["kind"] == "dismissed"],
        "fragments": sum(c["kind"] == "fragment" for c in ctx["candidates"]),
        "reviewed": len(unresolved(ctx)) if (note or "").strip() else 0,
        "places": {"searched": ctx["places"], "found": ctx["place_hits"]},
    }


def record(ctx, note=None):
    """Записать поиск и вердикт `absent` по одному документу."""
    text = build_text(ctx, note)
    stale = find.bare_old_spelling(text)
    if stale:
        raise ValueError("дореформенное написание вне кавычек: "
                         + ", ".join(stale))
    ident, surname = ctx["ident"], ctx["surname"]
    log_search(ident, surname, ctx["stem"], ctx["threshold"], ctx["normal_hits"])
    add_verdict(ident, surname, text, "absent", evidence=evidence(ctx, note))
    return text


def print_review(ctx):
    todo = unresolved(ctx)
    if not todo and not ctx["place_hits"]:
        return
    print("--- нужен разбор глазами ---")
    for label, pages in ctx["place_hits"].items():
        words = ", ".join(ctx.get("place_words", {}).get(label, []))
        print(f"  место «{label}»: {pages_phrase(pages)}"
              + (f" ({words})" if words else ""))
    for c in todo:
        mark = {"exact": "ТОЧНО", "partial": "ОБРЫВОК",
                "initials": "С ИНИЦИАЛАМИ — ОПЕЧАТКА?"}.get(c["kind"], "")
        print(f"  стр. {c['page']:>3}  {c['raw']!r:18} {mark}")
        print(f"        …{c['context']}…")
        print(f"        скан: {doc_dir(ctx['ident'])}/scans/p{c['page']:04d}.jpg")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=("draft", "accept"))
    ap.add_argument("surname")
    ap.add_argument("idents", nargs="+", help="один или несколько документов")
    ap.add_argument("--note", action="append", metavar="[ДОКУМЕНТ=]ТЕКСТ",
                    help="разбор кандидатов и мест, не отсеянных автоматически; "
                         "при нескольких документах — ДОКУМЕНТ=текст, "
                         "по одному --note на документ")
    a = ap.parse_args()
    notes = parse_notes(a.note, a.idents)

    contexts = [gather(i, a.surname) for i in a.idents]
    if a.action == "draft":
        for ctx in contexts:
            note = notes.get(ctx["ident"])
            print(f"=== {ctx['ident']} ===\n")
            print(build_text(ctx, note))
            print()
            print_review(ctx)
            for p in problems(ctx, note):
                print(f"  ! {p}")
        return

    # Сначала проверяются все документы, потом пишется хоть один:
    # половина записанных вердиктов хуже ни одного.
    refusal = [p for ctx in contexts
               for p in problems(ctx, notes.get(ctx["ident"]))]
    if refusal:
        sys.exit("вердикт не записан:\n  " + "\n  ".join(refusal))
    for ctx in contexts:
        record(ctx, notes.get(ctx["ident"]))
        catalog.refresh(ctx["ident"])
        print(f"{ctx['ident']}: записан вердикт absent")
    print(f"журнал: {journal.rebuild()}")
    for path in prune.sync_gitignore():
        print(f"  в .gitignore добавлена страница находки: {path}")


if __name__ == "__main__":
    main()
