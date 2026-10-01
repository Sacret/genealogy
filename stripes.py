#!/usr/bin/env python3
"""Сплошной проход газетных страниц вертикальными лентами.

Колонки из `rescue.py --cols` берут лишь часть листа, а страницы не ниже
порога штатно не перечитываются вовсе. Здесь каждая страница режется во
всю высоту на ленты шириной в пятую часть листа с шагом в десятую, и
каждая лента читается отдельно (`--psm 6`). Результат пишется в
`<id>/ocr_stripes/`; find.py в этот слой сам не заглядывает (он весит
больше всех прочих прочтений вместе и не идёт в репозиторий), его читают
глазами при разборе кандидатов.

    python3 stripes.py pn0026083                  # прочитать все страницы
    python3 stripes.py pn0026083 --pages 3,4      # только эти
    python3 stripes.py pn0026083 --words Кар Мог  # выписка слов по началу

Проход возобновляем: готовые страницы пропускаются, выброшенный скан
дотягивается из библиотеки. Когда прочитан весь документ, рядом с
метаданными пишется крошечный `stripes.json` — он-то и идёт в репозиторий.
Слой в вердикте называется «сплошным проходом», а сам он в чистой копии
отсутствует; маркер позволяет `audit.py` проверить, что проход был.
"""
import argparse
import collections
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from docstore import allow_big_scans, doc_dir, load_meta

TIMEOUT = 300           # одна лента, секунд: зависший tesseract не должен вешать пул
MARKER = "stripes.json"
WORD = re.compile(r"[^\W\d_]+")


def stripe_boxes(width: int):
    """Горизонтальные границы лент: ширина — пятая часть листа, шаг — десятая."""
    size, step = max(width // 5, 1), max(width // 10, 1)
    boxes, x = [], 0
    while True:
        boxes.append((x, min(x + size, width)))
        if x + size >= width:
            return boxes
        x += step


def read_stripe(image, lang, tmpdir, run=subprocess.run):
    """Текст одной ленты; сбой tesseract — ошибка, а не пустая лента.

    Пустой текст от упавшего процесса выглядел бы прочитанной полосой без
    единого слова, и по такому слою «не найдена» ничего бы не значила.
    """
    path = Path(tmpdir) / "stripe.png"
    image.save(path)
    r = run(["tesseract", str(path), "-", "-l", lang, "--psm", "6"],
            capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode:
        raise RuntimeError(f"tesseract: код {r.returncode}: "
                           f"{(r.stderr or '').strip()[:200]}")
    return r.stdout


def read_page(scan, lang, run=subprocess.run):
    from PIL import Image
    im = Image.open(scan).convert("L")
    w, h = im.size
    parts = []
    with tempfile.TemporaryDirectory(prefix="stripes-") as tmp:
        for left, right in stripe_boxes(w):
            parts.append(read_stripe(im.crop((left, 0, right, h)), lang, tmp, run))
    return "\n".join(parts)


def write_marker(ident, lang):
    """Отметить документ прочитанным целиком; None, если страниц не хватает."""
    d = doc_dir(ident)
    total = load_meta(ident).get("pages")
    done = len([f for f in (d / "ocr_stripes").glob("p*.txt")
                if f.stat().st_size > 0])
    if not total or done != total:
        return None
    marker = {"pages": done, "of": total, "lang": lang,
              "geometry": "лента в 1/5 ширины листа, шаг 1/10, psm 6",
              "date": datetime.now(timezone.utc).astimezone()
                              .isoformat(timespec="seconds")}
    (d / MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return marker


def process(ident, pages, lang, jobs, reader=read_page):
    """Прочитать страницы. Возвращает (прочитано, пропущено, {страница: ошибка})."""
    from fetch import ensure_page
    out = doc_dir(ident) / "ocr_stripes"
    out.mkdir(exist_ok=True)

    def one(n):
        dst = out / f"p{n:04d}.txt"
        if dst.exists() and dst.stat().st_size > 0:
            return n, "skip"
        try:
            text = reader(ensure_page(ident, n), lang)
        except Exception as e:      # одна страница не роняет прогон на сотню
            return n, f"{type(e).__name__}: {e}"
        # Через временное имя: оборванный процесс не оставит половину страницы,
        # которую повторный запуск счёл бы прочитанной.
        tmp = dst.with_suffix(".part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dst)
        return n, "ok"

    done = skipped = 0
    failed = {}
    with ThreadPoolExecutor(jobs) as ex:
        for i, (n, state) in enumerate(ex.map(one, pages), 1):
            if state == "ok":
                done += 1
            elif state == "skip":
                skipped += 1
            else:
                failed[n] = state
            if i % 10 == 0:
                print(f"  {i}/{len(pages)}", file=sys.stderr, flush=True)
    return done, skipped, failed


def words_by_prefix(texts, prefixes):
    """{начало: Counter слов}; texts — {страница: текст}. Регистр не важен."""
    found = {p: collections.defaultdict(set) for p in prefixes}
    for page, text in texts.items():
        for w in WORD.findall(text):
            low = w.lower()
            for p in prefixes:
                if low.startswith(p.lower()):
                    found[p][low].add(page)
    return found


def show_words(ident, prefixes, limit):
    out = doc_dir(ident) / "ocr_stripes"
    texts = {int(f.stem[1:]): f.read_text(encoding="utf-8", errors="replace")
             for f in sorted(out.glob("p*.txt"))}
    if not texts:
        sys.exit(f"нет {out} — сначала stripes.py {ident}")
    for prefix, words in words_by_prefix(texts, prefixes).items():
        ranked = sorted(words.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        print(f"{prefix}: {len(ranked)} разных слов")
        for word, pages in ranked[:limit]:
            print(f"  {word:24} стр. {', '.join(map(str, sorted(pages)))}")
        if len(ranked) > limit:
            print(f"  … ещё {len(ranked) - limit} (--limit)")


def main():
    allow_big_scans()
    ap = argparse.ArgumentParser()
    ap.add_argument("ident")
    ap.add_argument("--lang", default="rus")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--pages", help="только эти страницы: 3,4 или 1-4")
    ap.add_argument("--words", nargs="+", metavar="НАЧАЛО",
                    help="не читать, а выписать слова слоя по их началу")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()

    if a.words:
        return show_words(a.ident, a.words, a.limit)

    total = load_meta(a.ident).get("pages")
    if not total:
        sys.exit(f"{a.ident}: нет meta.json с числом страниц — сначала fetch.py")
    if a.pages:
        from fetch import parse_pages
        pages = [p for p in parse_pages(a.pages) if 1 <= p <= total]
    else:
        pages = list(range(1, total + 1))
    print(f"{load_meta(a.ident).get('title', a.ident)}: ленты, {len(pages)} стр.")
    done, skipped, failed = process(a.ident, pages, a.lang, a.jobs)
    print(f"готово: прочитано {done}, уже было {skipped}, ошибок {len(failed)}")
    for n, why in sorted(failed.items()):
        print(f"  стр. {n}: {why}", file=sys.stderr)
    if failed:
        sys.exit(1)
    if write_marker(a.ident, a.lang):
        print(f"документ прочитан целиком, отмечено в {MARKER}")


if __name__ == "__main__":
    main()
