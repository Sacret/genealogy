#!/usr/bin/env python3
"""Пакетное распознавание документа. Возобновляемое, параллельное по ядрам.

Один проход Tesseract даёт и текст страницы, и среднюю уверенность по
словам (см. tess.py). Уверенность откладывается в `ocr_stats.json`, и
`quality.py` берёт её оттуда, а не распознаёт том второй раз.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import tess
from docstore import doc_dir, load_meta

STATS = "ocr_stats.json"


def load_stats(folder, lang, psm):
    """{страница: {conf, words}} прежних проходов тем же режимом, иначе {}.

    Чужой режим (другой psm или язык) не годится: уверенность измерена
    по другому тексту, и quality.json по ней назвал бы слабыми не те
    страницы.
    """
    path = folder / STATS
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    if data.get("lang") != lang or str(data.get("psm")) != str(psm):
        return {}
    return data.get("pages", {})


def save_stats(folder, lang, psm, pages):
    (folder / STATS).write_text(json.dumps(
        {"lang": lang, "psm": str(psm), "pages": pages},
        ensure_ascii=False, indent=1), encoding="utf-8")


def run(job):
    """(страница, статистика | None, ошибка | None); None — страница была готова."""
    img, dst, lang, psm = job
    n = int(img.stem[1:])
    if dst.exists() and dst.stat().st_size > 0:
        return n, None, None
    try:
        text, conf, words = tess.recognize(img, lang, psm)
    except Exception as e:      # tess.TesseractError, TimeoutExpired, OSError
        return n, None, f"{type(e).__name__}: {e}"
    # Через временное имя: оборванный процесс не оставит половину страницы,
    # которую повторный запуск счёл бы распознанной.
    tmp = dst.with_suffix(".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dst)
    return n, {"conf": conf, "words": words}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ident", help="идентификатор документа, напр. bv0000407")
    ap.add_argument("--lang", default="rus")
    ap.add_argument("--psm", default="6")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--redo", action="store_true", help="распознать заново поверх старого")
    a = ap.parse_args()

    d = doc_dir(a.ident)
    out = d / "ocr"
    out.mkdir(parents=True, exist_ok=True)
    scans = sorted((d / "scans").glob("p*.jpg"))
    if not scans:
        sys.exit(f"нет сканов в {d / 'scans'} — сначала fetch.py")
    if a.redo:
        for f in out.glob("p*.txt"):
            f.unlink()
        (d / STATS).unlink(missing_ok=True)

    jobs = [(f, out / (f.stem + ".txt"), a.lang, a.psm) for f in scans]
    print(f"{load_meta(a.ident).get('title', a.ident)}: {len(jobs)} страниц, "
          f"{a.lang} --psm {a.psm}")
    fresh, failed = 0, {}
    stats = load_stats(d, a.lang, a.psm)
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, (n, stat, error) in enumerate(ex.map(run, jobs), 1):
            if error:
                failed[n] = error
            elif stat is not None:
                fresh += 1
                stats[str(n)] = stat
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)
    if stats:
        save_stats(d, a.lang, a.psm, stats)
    print(f"готово: {len(jobs)} страниц, заново распознано {fresh}, "
          f"ошибок {len(failed)}")
    if failed:
        for n, why in sorted(failed.items()):
            print(f"  стр. {n}: {why}", file=sys.stderr)
        sys.exit(f"не распознаны стр. {', '.join(map(str, sorted(failed)))} — "
                 "повторный запуск дочитает")


if __name__ == "__main__":
    main()
