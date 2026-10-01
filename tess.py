"""Единственное место, где мы запускаем Tesseract целой страницей.

Раньше `ocr_pages.py` распознавал страницу ради текста, а `quality.py`
через несколько минут распознавал её же второй раз ради колонки `conf` в
TSV. Tesseract умеет отдать оба вывода за один проход
(`tesseract img base -l rus txt tsv`), так что текст и уверенность теперь
берутся из одного распознавания: первый проход по тому стал почти вдвое
дешевле, а замер надёжности — бесплатным.

Заодно здесь живут проверки, которых не было в каждом из мест: упавший
или зависший процесс — ошибка (`TesseractError`), а не пустой текст,
который выглядел бы страницей без единого слова.
"""

import csv
import io
import os
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 600       # одна страница, секунд: зависший процесс не должен вешать пул

# Параллельные задачи по числу ядер и без того занимают все ядра; если
# каждая из них ещё и многопоточна, потоки OpenMP мешают друг другу и
# прогон идёт медленнее, а не быстрее. Так советует и документация
# Tesseract.
ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


class TesseractError(RuntimeError):
    pass


def check(result, what):
    if result.returncode:
        err = result.stderr
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        raise TesseractError(f"{what}: код {result.returncode}: "
                             f"{(err or '').strip()[:200]}")
    return result


def parse_tsv(tsv: str):
    """(средняя уверенность, число слов) по TSV Tesseract.

    Слова — строки с непустым текстом; уверенность −1 у служебных строк
    блоков и строк не считается. Страница без слов даёт (0.0, 0).
    """
    rows = [r for r in csv.DictReader(io.StringIO(tsv), delimiter="\t",
                                      quoting=csv.QUOTE_NONE)
            if (r.get("text") or "").strip()]
    confs = [float(r["conf"]) for r in rows if float(r["conf"]) >= 0]
    if not confs:
        return 0.0, 0
    return sum(confs) / len(confs), len(rows)


def recognize(img, lang, psm, run=subprocess.run):
    """Текст страницы и её статистика за один проход: (text, conf, words)."""
    with tempfile.TemporaryDirectory(prefix="tess-") as tmp:
        base = Path(tmp) / "page"
        check(run(["tesseract", str(img), str(base), "-l", lang,
                   "--psm", str(psm), "txt", "tsv"],
                  capture_output=True, timeout=TIMEOUT, env=ENV),
              f"tesseract {Path(img).name}")
        text = (base.with_suffix(".txt")).read_text(encoding="utf-8",
                                                    errors="replace")
        conf, words = parse_tsv(base.with_suffix(".tsv")
                                .read_text(encoding="utf-8", errors="replace"))
    return text, conf, words


def text_only(img, lang, psm, run=subprocess.run):
    """Только текст — для полос, колонок и бинаризованных страниц."""
    r = check(run(["tesseract", str(img), "-", "-l", lang, "--psm", str(psm)],
                  capture_output=True, timeout=TIMEOUT, env=ENV),
              f"tesseract {Path(img).name}")
    return r.stdout.decode("utf-8", "replace")
